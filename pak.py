# Source: http://www.metroid2002.com/retromodding/wiki/PAK_(Metroid_Prime)

import dataclasses
import struct

from dgrp import DGRP
from dumb import DUMB
from hint import HINT
from scan import SCAN
from strg import STRG
from tree import ScanTree
from util import unpack_int, unpack_ascii, pack_int, pack_ascii

__all__ = ("NamedResourceTable", "ResourceTable", "UnimplementedResource", "PAK")


@dataclasses.dataclass(frozen=True)
class NamedResourceTable:
    _struct = struct.Struct(">4sII")

    asset_type: str
    asset_ID: int
    name_length: int
    name: str

    @classmethod
    def from_packed(cls, packed: bytes):
        asset_type_bytes, asset_ID, name_length = cls._struct.unpack(packed[:12])
        return cls(
            unpack_ascii(asset_type_bytes),
            asset_ID,
            name_length,
            unpack_ascii(packed[12:12+name_length]),
        )

    @property
    def packed_size(self) -> int:
        return len(self.packed())

    def packed(self) -> bytes:
        return b"".join((
            self._struct.pack(pack_ascii(self.asset_type), self.asset_ID, self.name_length),
            pack_ascii(self.name),
        ))


@dataclasses.dataclass(frozen=True)
class ResourceTable:
    _struct = struct.Struct(">I4sIII")

    compressed: bool
    asset_type: str
    asset_ID: int
    size: int
    offset: int

    @classmethod
    def from_packed(cls, packed: bytes):
        compressed_int, asset_type_bytes, asset_ID, size, offset = cls._struct.unpack(packed)
        return cls(bool(compressed_int), unpack_ascii(asset_type_bytes), asset_ID, size, offset)

    @property
    def packed_size(self) -> int:
        return len(self.packed())

    def packed(self) -> bytes:
        return self._struct.pack(
            int(self.compressed),
            pack_ascii(self.asset_type),
            self.asset_ID,
            self.size,
            self.offset,
        )


@dataclasses.dataclass(frozen=True)
class UnimplementedResource:
    data: bytes = dataclasses.field(repr=False)

    @classmethod
    def from_packed(cls, packed: bytes):
        return cls(packed)

    @property
    def packed_size(self) -> int:
        return len(self.data)

    def packed(self) -> bytes:
        return self.data


def aligned_to_32_bytes(bytes_to_align: bytes):
    padding = ((32 - (len(bytes_to_align) % 32)) % 32) * b"\xff"
    return bytes_to_align + padding

@dataclasses.dataclass(frozen=True)
class PAK:
    _struct = struct.Struct(">HHII")

    major_version: int
    minor_version: int
    unused: int
    named_resource_count: int
    named_resource_tables: tuple = dataclasses.field(repr=False)
    resource_count: int
    resource_tables: tuple = dataclasses.field(repr=False)
    resources: tuple = dataclasses.field(repr=False)

    asset_classes = {
        "DGRP": DGRP,
        "DUMB": DUMB,
        "HINT": HINT,
        "SCAN": SCAN,
        "STRG": STRG,
    }

    def __post_init__(self):
        asset_ID_to_index_map = {}
        for index, resource_table in enumerate(self.resource_tables):
            asset_ID_to_index_map[resource_table.asset_ID] = index
        object.__setattr__(self, "_asset_ID_to_index_map", asset_ID_to_index_map)

    @classmethod
    def from_packed(cls, packed: bytes):
        major_version, minor_version, unused, named_resource_count = cls._struct.unpack(packed[:12])

        offset = 12
        named_resource_tables = []
        for i in range(named_resource_count):
            table = NamedResourceTable.from_packed(packed[offset:])
            named_resource_tables.append(table)
            offset += 12 + table.name_length

        resource_count = unpack_int(packed[offset:offset+4])
        offset += 4
        resource_tables = []
        for i in range(resource_count):
            resource_tables.append(ResourceTable.from_packed(packed[offset:offset+20]))
            offset += 20

        end_of_resource_tables_offset = offset
        resources = []
        for resource_table in resource_tables:
            if resource_table.asset_ID == 0x95B61279:
                asset_class = ScanTree
            else:
                asset_class = cls.asset_classes.get(resource_table.asset_type, UnimplementedResource)
            offset, size = resource_table.offset, resource_table.size
            resources.append(asset_class.from_packed(packed[offset:offset+size]))

        return cls(
            major_version,
            minor_version,
            unused,
            named_resource_count,
            tuple(named_resource_tables),
            resource_count,
            tuple(resource_tables),
            tuple(resources),
        )

    @property
    def packed_content_before_resources_size(self) -> int:
        named_resource_tables_size = \
            sum(named_resource_table.packed_size for named_resource_table in self.named_resource_tables)
        resource_tables_size = sum(resource_table.packed_size for resource_table in self.resource_tables)

        return 2 + 2 + 4 + 4 + named_resource_tables_size + 4 + resource_tables_size

    @property
    def packed_padding_before_resources_size(self) -> int:
        return (32 - (self.packed_content_before_resources_size % 32)) % 32

    @property
    def packed_size(self) -> int:
        resources_size = sum(resource.packed_size for resource in self.resources)
        return self.packed_content_before_resources_size + self.packed_padding_before_resources_size + resources_size

    def packed(self) -> bytes:
        return b"".join((
            self._struct.pack(self.major_version, self.minor_version, self.unused, self.named_resource_count),
            *(named_resource_table.packed() for named_resource_table in self.named_resource_tables),
            pack_int(self.resource_count),
            *(resource_table.packed() for resource_table in self.resource_tables),
            b"\x00" * self.packed_padding_before_resources_size,
            *(aligned_to_32_bytes(resource.packed()) for resource in self.resources),
        ))

    def get_resource_by_asset_ID(self, asset_ID: int):
        return self.resources[self._asset_ID_to_index_map[asset_ID]]

    def with_resource_inserted(self, index: int, asset_ID: int, new_resource):
        if index == self.resource_count:
            new_resource_table_offset = self.resource_tables[-1].offset + self.resource_tables[-1].size
        else:
            new_resource_table_offset = self.resource_tables[index].offset
        new_resource_table = ResourceTable(
            False, # TODO: Support compressing resources
            new_resource.asset_type,
            asset_ID,
            new_resource.packed_size,
            new_resource_table_offset,
        )

        new_resource_tables = [*self.resource_tables[:index], new_resource_table]
        for resource_table in self.resource_tables[index:]:
            new_resource_tables.append(
                dataclasses.replace(resource_table, offset=resource_table.offset+new_resource.packed_size)
            )

        return dataclasses.replace(
            self,
            resource_count=self.resource_count+1,
            resource_tables=tuple(new_resource_tables),
            resources=(*self.resources[:index], new_resource, *self.resources[index:])
        )

    def with_resource_appended(self, asset_ID: int, new_resource):
        return self.with_resource_inserted(self.resource_count, asset_ID, new_resource)

    def with_resource_removed(self, index: int):
        removed_resource = self.resources[index]
        new_resource_tables = list(self.resource_tables[:index])
        for resource_table in self.resource_tables[index+1:]:
            new_resource_tables.append(
                dataclasses.replace(resource_table, offset=resource_table.offset-removed_resource.packed_size)
            )

        return dataclasses.replace(
            self,
            resource_count=self.resource_count-1,
            resource_tables=tuple(new_resource_tables),
            resources=self.resources[:index] + self.resources[index+1:],
        )

    def with_resource_removed_by_asset_ID(self, asset_ID: int):
        return self.with_resource_removed(self, self._asset_ID_to_index_map[asset_ID])

    def with_resource_replaced(self, index: int, new_resource):
        asset_ID = self.resource_tables[index].asset_ID
        return self.with_resource_removed(index).with_resource_inserted(index, asset_ID, new_resource)

    def with_resource_replaced_by_asset_ID(self, asset_ID: int, new_resource):
        index = self._asset_ID_to_index_map[asset_ID]
        return self.with_resource_removed(index).with_resource_inserted(index, asset_ID, new_resource)