# Copyright (c) 2023 Adam Souzis
# SPDX-License-Identifier: MIT
"""
Expose TOSCA units as Python objects
Usage:

>>> from tosca import mb
>>> one_mb = 1 * mb
>>> one_mb
1000000.0
>>> one_mb.as_unit
1.0
>>> one_mb.to_yaml()
'1.0 MB'
"""

import math


class _Scalar(float):
    scale = 1.0

    @property
    def as_unit(self) -> float:
        return self / self.scale

    def to_yaml(self, dict_cls=dict) -> str:
        "return this value and this type's TOSCA unit suffix, eg. 10 kB"
        val = self.as_unit
        as_int = math.floor(val)
        if val == as_int:
            val = as_int  # whole number, treat as int
        # strip "_Scalar" suffix
        return f"{val} {self.__class__.__name__[:-7]}"

    # def __str__(self) -> str:
    #     return str(float(self))

    # def __repr__(self) -> str:
    #     return f"{self.__class__.__name__}({super().__repr__()})"


class Size(_Scalar):
    tosca_name = "scalar-unit.size"


class Frequency(_Scalar):
    tosca_name = "scalar-unit.frequency"


class Time(_Scalar):
    tosca_name = "scalar-unit.time"


class Bitrate(_Scalar):
    tosca_name = "scalar-unit.bitrate"


class _Unit:
    def __init__(self, scalar_type):
        self.scalar_type = scalar_type

    def __rmul__(self, other):
        return self.scalar_type(self.scalar_type.scale * other)


class B_Scalar(Size):
    scale = 1


B = _Unit(B_Scalar)
b = B


class kB_Scalar(Size):
    scale = 1000


kB = _Unit(kB_Scalar)
kb = kB
KB = kB


class KiB_Scalar(Size):
    scale = 1024


KiB = _Unit(KiB_Scalar)
kib = KiB
KIB = KiB


class MB_Scalar(Size):
    scale = 1000000


MB = _Unit(MB_Scalar)
mb = MB


class MiB_Scalar(Size):
    scale = 1048576


MiB = _Unit(MiB_Scalar)
mib = MiB
MIB = MiB


class GB_Scalar(Size):
    scale = 1000000000


GB = _Unit(GB_Scalar)
gb = GB


class GiB_Scalar(Size):
    scale = 1073741824


GiB = _Unit(GiB_Scalar)
gib = GiB
GIB = GiB


class TB_Scalar(Size):
    scale = 1000000000000


TB = _Unit(TB_Scalar)
tb = TB


class TiB_Scalar(Size):
    scale = 1099511627776


TiB = _Unit(TiB_Scalar)
tib = TiB
TIB = TiB


class d_Scalar(Time):
    scale = 86400


d = _Unit(d_Scalar)
D = d


class h_Scalar(Time):
    scale = 3600


h = _Unit(h_Scalar)
H = h


class m_Scalar(Time):
    scale = 60


m = _Unit(m_Scalar)
M = m


class s_Scalar(Time):
    scale = 1


s = _Unit(s_Scalar)
S = s


class ms_Scalar(Time):
    scale = 0.001


ms = _Unit(ms_Scalar)
MS = ms


class us_Scalar(Time):
    scale = 1e-06


us = _Unit(us_Scalar)
US = us


class ns_Scalar(Time):
    scale = 1e-09


ns = _Unit(ns_Scalar)
NS = ns


class Hz_Scalar(Frequency):
    scale = 1


Hz = _Unit(Hz_Scalar)
hz = Hz
HZ = Hz


class kHz_Scalar(Frequency):
    scale = 1000


kHz = _Unit(kHz_Scalar)
khz = kHz
KHZ = kHz


class MHz_Scalar(Frequency):
    scale = 1000000


MHz = _Unit(MHz_Scalar)
mhz = MHz
MHZ = MHz


class GHz_Scalar(Frequency):
    scale = 1000000000


GHz = _Unit(GHz_Scalar)
ghz = GHz
GHZ = GHz


class bps_Scalar(Bitrate):
    scale = 1


bps = _Unit(bps_Scalar)
BPS = bps


class Kbps_Scalar(Bitrate):
    scale = 1000


Kbps = _Unit(Kbps_Scalar)
kbps = Kbps
KBPS = Kbps


class Kibps_Scalar(Bitrate):
    scale = 1024


Kibps = _Unit(Kibps_Scalar)
kibps = Kibps
KIBPS = Kibps


class Mbps_Scalar(Bitrate):
    scale = 1000000


Mbps = _Unit(Mbps_Scalar)
mbps = Mbps
MBPS = Mbps


class Mibps_Scalar(Bitrate):
    scale = 1048576


Mibps = _Unit(Mibps_Scalar)
mibps = Mibps
MIBPS = Mibps


class Gbps_Scalar(Bitrate):
    scale = 1000000000


Gbps = _Unit(Gbps_Scalar)
gbps = Gbps
GBPS = Gbps


class Gibps_Scalar(Bitrate):
    scale = 1073741824


Gibps = _Unit(Gibps_Scalar)
gibps = Gibps
GIBPS = Gibps


class Tbps_Scalar(Bitrate):
    scale = 1000000000000


Tbps = _Unit(Tbps_Scalar)
tbps = Tbps
TBPS = Tbps


class Tibps_Scalar(Bitrate):
    scale = 1099511627776


Tibps = _Unit(Tibps_Scalar)
tibps = Tibps
TIBPS = Tibps


# generated by:
# import unfurl
# from toscaparser.elements.scalarunit import ScalarUnit_Size, ScalarUnit_Time, ScalarUnit_Frequency, ScalarUnit_Bitrate

# for scalar_cls in [ScalarUnit_Size, ScalarUnit_Time, ScalarUnit_Frequency, ScalarUnit_Bitrate]:
#     base = scalar_cls.__name__[len("ScalarUnit_"):]
#     for name, scale in scalar_cls.SCALAR_UNIT_DICT.items():
#         print(f"\nclass {name}_Scalar({base}):\n    scale = {scale}\n")
#         print(f"{name} = _Unit({name}_Scalar)")
#         if name != name.lower():
#             print(f"{name.lower()} = {name}")
#         if name != name.upper():
#             print(f"{name.upper()} = {name}")