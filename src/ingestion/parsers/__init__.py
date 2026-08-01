"""Per-source parsers: raw vendor file in, common-schema long frame out.

Every parser here is pure -- it reads one path and returns a DataFrame with
`schema.OBSERVATION_COLUMNS`, with no cross-source knowledge. Anything needing
two sources at once (back-dating HPLC injections to their sample draw time)
lives in `normalize`, one layer up.
"""

from .analytical import parse_hplc_file
from .auxiliary import parse_capacitance_file, parse_offgas_file
from .bioreactor import parse_ambr_file, parse_dasgip_file
from .offline import parse_biomass_file

__all__ = [
    "parse_ambr_file",
    "parse_biomass_file",
    "parse_capacitance_file",
    "parse_dasgip_file",
    "parse_hplc_file",
    "parse_offgas_file",
]
