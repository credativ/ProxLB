# Minimal pandas stub for proxlb packaging.
#
# ortools' cp_model.py and model_builder.py import pandas at module load time
# to define a type alias (Union[pd.Index, pd.Series]) and for isinstance checks
# in methods that accept pd.Series/pd.Index arguments.
#
# proxlb never passes pandas objects to ortools, so those code paths are never
# reached. This stub satisfies the import-time type references at a fraction
# of the real package size (~27 MB saved).


class Series:
    """Stub — proxlb never passes pd.Series objects to ortools."""

    def __init__(self, *args, **kwargs):
        raise ImportError(
            "Full pandas is not available in this proxlb installation. "
            "This build is optimised for proxlb's usage of ortools CP-SAT."
        )


class Index:
    """Stub — proxlb never passes pd.Index objects to ortools."""

    def __init__(self, *args, **kwargs):
        raise ImportError(
            "Full pandas is not available in this proxlb installation. "
            "This build is optimised for proxlb's usage of ortools CP-SAT."
        )


class DataFrame:
    """Stub."""

    def __init__(self, *args, **kwargs):
        raise ImportError(
            "Full pandas is not available in this proxlb installation."
        )


NA = None
NaT = None
__version__ = "0.0.0-proxlb-stub"
