# coding=utf-8
from __future__ import unicode_literals

import logging
from threading import Lock

from vnxCliApi.vnx.cli import raise_if_err
from vnxCliApi.vnx.enums import VNXSPEnum, VNXPortType, has_error, VNXError
from vnxCliApi.vnx.resource.lun import VNXLun
from vnxCliApi.vnx.resource.port import VNXHbaPort
from vnxCliApi.vnx.resource.resource import VNXResource, VNXCliResourceList
from vnxCliApi import exception as ex

__author__ = 'Cedric Zhuang'

log = logging.getLogger(__name__)


class VNXStorageGroup(VNXResource):
    def __init__(self, name=None, cli=None):
        super(VNXStorageGroup, self).__init__()
        self._cli = cli
        self._name = name

        self._uid = ''
        self._hba_port_map = []
        self._conn = None
        self._hlu_lock = Lock()

    def _get_raw_resource(self):
        return self._cli.get_sg(name=self._name)

    @classmethod
    def get(cls, cli, name=None):
        if name is None:
            ret = VNXStorageGroupList(cli)
        else:
            ret = VNXStorageGroup(name, cli)
        return ret

    @classmethod
    def create(cls, name, cli):
        cli.create_sg(name)
        return VNXStorageGroup(name, cli)

    def remove(self):
        self._cli.remove_sg(self._get_name())

    def has_hlu(self, hlu):
        return hlu in self.alu_hlu_map.values()

    def has_alu(self, lun):
        alu = VNXLun.get_id(lun)
        return alu in self.alu_hlu_map.keys()

    def get_hlu(self, lun):
        alu = VNXLun.get_id(lun)
        return self.alu_hlu_map.get(alu, None)

    def is_valid(self):
        return len(self.name) > 0 and len(self.uid) > 0

    @property
    def hba_port_map(self):
        if not self._hba_port_map:
            self.hba_port_map = self.hba_sp_pairs
        return self._hba_port_map

    @hba_port_map.setter
    def hba_port_map(self, hba_sp_pairs):
        def _process_cli_output(value):
            port = VNXHbaPort.from_storage_group_hba(value)
            hba = value.uid
            self._hba_port_map.append((hba, port))

        if hba_sp_pairs is not None:
            for item in hba_sp_pairs:
                _process_cli_output(item)

    @property
    def port_list(self):
        return tuple(set(map(lambda x: x[1], self.hba_port_map)))

    @property
    def initiator_uid_list(self):
        return tuple(set(map(lambda x: x.uid, self.hba_sp_pairs)))

    def get_initiator_uids(self, port_type=None):
        ret = []
        for hba, port in self.hba_port_map:
            if port_type is not None:
                if port.type == port_type:
                    ret.append(hba)
            else:
                ret.append(hba)
        return tuple(set(ret))

    def get_ports(self, initiator_uid):
        ret = []
        for hba, port in self.hba_port_map:
            if hba == initiator_uid:
                ret.append(port)
        return tuple(set(ret))

    _hlu_full = None
    _max_hlu = 255

    @classmethod
    def get_max_luns_per_sg(cls):
        return cls._max_hlu

    @classmethod
    def set_max_luns_per_sg(cls, value):
        log.info('Update max LUNs per storage group to: {}'
                 .format(value))
        cls._max_hlu = value
        cls._hlu_full = None

    @classmethod
    def _hlu_full_set(cls):
        if cls._hlu_full is None:
            cls._hlu_full = set(range(1, cls.get_max_luns_per_sg() + 1))
        return set(cls._hlu_full)

    def _get_hlu_to_add(self, alu):
        ret = None
        with self._hlu_lock:
            remain = self._hlu_full_set() - set(self.alu_hlu_map.values())
            if len(remain) == 0:
                raise ex.VNXNoHluAvailableError(
                    'no hlu number available for attach.')
            ret = remain.pop()
            self.alu_hlu_map[alu] = ret
        return ret

    def _remove_alu(self, alu):
        ret = None
        with self._hlu_lock:
            if self.has_alu(alu):
                ret = self.alu_hlu_map.pop(alu)
        return ret

    class _HluOccupiedError(Exception):
        pass

    def attach_hlu(self, lun):
        lun = VNXLun.get_id(lun)
        while True:
            alu = self._get_hlu_to_add(lun)
            out = self._cli.sg_add_hlu(self._get_name(), alu, lun)
            if has_error(out, VNXError.SG_HOST_LUN_USED):
                self.update()
                continue
            break
        return VNXLun(self._cli, lun)

    def detach_hlu(self, lun):
        alu = VNXLun.get_id(lun)
        out = self._cli.sg_remove_hlu(self._get_name(), alu)
        raise_if_err(out, ex.VNXStorageGroupError,
                     'failed to detach alu {}.'.format(alu))
        self._remove_alu(alu)

    def connect_host(self, host):
        out = self._cli.sg_connect_host(self._get_name(), host)
        raise_if_err(out, ex.VNXStorageGroupError,
                     'failed to connect host {}.'.format(host))

    def disconnect_host(self, host):
        out = self._cli.sg_disconnect_host(self._get_name(), host)
        raise_if_err(out, ex.VNXStorageGroupError,
                     'failed to disconnect host {}.'.format(host))

    @property
    def uid(self):
        return self.wwn


class VNXStorageGroupList(VNXCliResourceList):
    @classmethod
    def get_resource_class(cls):
        return VNXStorageGroup

    def __init__(self, cli=None):
        super(VNXStorageGroupList, self).__init__(cli)
        self._sg_map = {}

    def add_sg(self, sg):
        self._sg_map[sg.name] = sg

    def _get_raw_resource(self):
        return self._cli.get_sg()


class VNXStorageGroupHBA(VNXResource):
    @property
    def sp(self):
        return VNXSPEnum.from_str(self.hba[1])

    @property
    def port_id(self):
        return int(self.hba[2])

    @property
    def uid(self):
        return self.hba[0]

    @property
    def vlan(self):
        ret = None
        sp_port = self.sp_port
        if 'v' in sp_port:
            ret = int(sp_port[sp_port.find('v') + 1:])
        return ret

    @property
    def port_type(self):
        ret = None
        if '.' in self.uid:
            ret = VNXPortType.ISCSI
        elif ':' in self.uid:
            ret = VNXPortType.FC
        return ret


class VNXStorageGroupHBAList(VNXCliResourceList):
    @classmethod
    def get_resource_class(cls):
        return VNXStorageGroupHBA
