"""Which runs are legitimately comparable with which.

A cohort is a set of runs that differ only in the ways you are interested in.
The seven synthetic runs form two: three *E. coli* batch runs at 5 L and four
CHO fed-batch runs at 0.25 L. Overlaying one on the other would produce a chart
that is easy to draw and impossible to interpret -- different organism,
different feeding regime, twenty times the volume, and nearly three times the
duration.

So the comparison view never offers that. Cohorts are derived from the run
manifest rather than hardcoded, which means literature-derived runs will sort
themselves into their own cohorts later without a code change here.
"""

from dataclasses import dataclass

import pandas as pd

# Runs are comparable when these agree. Volume and duration deliberately are
# not included: a scale-up series or a run stopped early is still a legitimate
# comparison, and excluding them would fragment cohorts into singletons.
COHORT_KEYS = ("system", "strain", "mode")


@dataclass(frozen=True)
class Cohort:
    key: tuple
    run_ids: tuple[str, ...]
    strain: str
    mode: str
    system: str

    @property
    def label(self) -> str:
        return f"{self.strain} · {self.mode} ({len(self.run_ids)} runs)"

    @property
    def held_constant(self) -> str:
        return f"{self.strain}, {self.mode}, {self.system} control system"


def build_cohorts(runs: pd.DataFrame) -> list[Cohort]:
    """Group the manifest into comparable sets, largest first."""
    cohorts = []
    for key, group in runs.groupby(list(COHORT_KEYS), sort=True):
        first = group.iloc[0]
        cohorts.append(
            Cohort(
                key=tuple(key),
                run_ids=tuple(group["run_id"]),
                strain=first["strain"],
                mode=first["mode"],
                system=first["system"],
            )
        )
    return sorted(cohorts, key=lambda c: (-len(c.run_ids), c.label))


def cohort_for_run(cohorts: list[Cohort], run_id: str) -> Cohort:
    for cohort in cohorts:
        if run_id in cohort.run_ids:
            return cohort
    raise KeyError(f"run {run_id!r} belongs to no cohort")
