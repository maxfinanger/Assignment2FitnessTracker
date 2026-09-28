"""Write results to disk.

Output is deterministic: files are always overwritten, contain no timestamps
and list sessions in input order, so running the program twice gives identical
files and never needs manual cleanup.
"""

import csv
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Union

from .exceptions import DataFileError

PathLike = Union[str, Path]

SUMMARY_COLUMNS = (
    "session_id",
    "participant_id",
    "participant_name",
    "source_file",
    "rows_received",
    "rows_usable",
    "rows_rejected",
    "invalid_fraction",
    "avg_signal_quality",
    "low_confidence_windows",
    "avg_heart_rate",
    "avg_skin_response",
    "avg_temperature",
    "avg_activity_level",
    "heart_rate_delta",
    "skin_response_delta",
    "temperature_delta",
    "is_recovering",
    "classification",
    "explanation",
)


def prepare_output_directory(directory: PathLike) -> Path:
    """Create the output directory (and parents) if it does not exist."""
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except FileExistsError as error:       # a *file* is in the way
        raise DataFileError(directory, "exists but is not a directory") from error
    except PermissionError as error:
        raise DataFileError(directory, "permission denied creating output directory") from error
    except OSError as error:
        raise DataFileError(directory, f"cannot create output directory ({error.strerror})") from error
    return directory


def summary_row(result: Dict) -> List:
    """Flatten one result dictionary into one CSV row (None becomes blank)."""
    quality = result["data_quality"]
    summaries = result["summaries"]
    comparison = result["baseline_comparison"]

    def average(field_name):
        summary = summaries[field_name]
        return summary["average"] if summary else None

    values = [
        result["session_id"],
        result["participant"]["participant_id"],
        result["participant"].get("name", ""),
        result["source_file"],
        quality["total_observations"],
        quality["usable_observations"],
        len(quality["rejected_rows"]),
        quality["invalid_fraction"],
        quality["average_signal_quality"],
        len(quality["low_confidence_timestamps"]),
        average("heart_rate"),
        average("skin_response"),
        average("temperature"),
        average("activity_level"),
        comparison["heart_rate_delta"],
        comparison["skin_response_delta"],
        comparison["temperature_delta"],
        result["recovery"]["is_recovering"],
        result["classification"]["label"],
        result["classification"]["explanation"],
    ]
    return ["" if value is None else value for value in values]


def write_summary_csv(path: PathLike, results: Iterable[Dict]) -> Path:
    """Write analysis_summary.csv: a header plus one row per session."""
    path = Path(path)
    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(SUMMARY_COLUMNS)
            for result in results:
                writer.writerow(summary_row(result))
    except PermissionError as error:
        raise DataFileError(path, "permission denied writing file") from error
    except OSError as error:
        raise DataFileError(path, f"cannot write file ({error.strerror})") from error
    return path


def write_text(path: PathLike, text: str) -> Path:
    """Write a text report (UTF-8), replacing any previous version."""
    path = Path(path)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
    except PermissionError as error:
        raise DataFileError(path, "permission denied writing file") from error
    except OSError as error:
        raise DataFileError(path, f"cannot write file ({error.strerror})") from error
    return path
