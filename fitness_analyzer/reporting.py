"""Presentation functions: turn results into readable text.

Formatting is kept entirely separate from analysis (and from file writing, see
``writers.py``) so that the rules can be changed without touching the layout,
and the layout without risking the numbers. Every function here returns a
string; none of them touches the file system.
"""

from typing import Dict, List, Optional, Sequence

from .loader import LoadResult
from .models import RejectedRecord

LINE_WIDTH = 66


# --- one session -----------------------------------------------------------


def format_measurement(name: str, summary: Optional[Dict], unit: str = "") -> str:
    """Format one average/minimum/maximum row, or note that it is unavailable."""
    label = name.replace("_", " ") + ":"
    if summary is None:
        return f"  {label:<18} no usable data"
    suffix = f" {unit}" if unit else ""
    return (
        f"  {label:<18} avg {summary['average']}{suffix}"
        f"   min {summary['minimum']}{suffix}   max {summary['maximum']}{suffix}"
    )


def format_data_quality(quality: Dict) -> str:
    lines = [
        f"  rows received:      {quality['total_observations']}",
        f"  rows usable:        {quality['usable_observations']}",
        f"  rows rejected:      {len(quality['rejected_rows'])} ({quality['invalid_fraction']:.0%})",
    ]
    if quality["average_signal_quality"] is not None:
        lines.append(f"  avg signal quality: {quality['average_signal_quality']}")
    if quality["low_confidence_timestamps"]:
        stamps = ", ".join(str(t) for t in quality["low_confidence_timestamps"])
        lines.append(f"  low confidence at:  t={stamps}")
    return "\n".join(lines)


def format_rejections(quality: Dict) -> str:
    """List every rejected row of a session and why it was rejected."""
    if not quality["rejected_rows"]:
        return "  none"
    lines = []
    for entry in quality["rejected_rows"]:
        where = f"row {entry['row_number']}" if entry["row_number"] is not None else f"t={entry['timestamp']}"
        lines.append(f"  {where}:")
        for issue in entry["issues"]:
            lines.append(f"    - {issue}")
    return "\n".join(lines)


def build_report(result: Dict) -> str:
    """Render a complete, readable report for one analysed session."""
    participant = result["participant"]
    comparison = result["baseline_comparison"]
    recovery = result["recovery"]
    classification = result["classification"]

    who = participant["participant_id"]
    if participant.get("name"):
        who += f" ({participant['name']})"

    lines = []
    lines.append("=" * LINE_WIDTH)
    lines.append(f"SESSION {result['session_id']}  |  participant {who}")
    lines.append(f"source: {result['source_file']}")
    lines.append("=" * LINE_WIDTH)

    lines.append("")
    lines.append("PERSONAL REFERENCE VALUES")
    lines.append(f"  heart rate:    {participant['baseline_heart_rate']} bpm")
    lines.append(f"  skin response: {participant['baseline_skin_response']}")
    lines.append(f"  temperature:   {participant['baseline_temperature']} C")

    lines.append("")
    lines.append("DATA QUALITY")
    lines.append(format_data_quality(result["data_quality"]))

    if result["data_quality"]["rejected_rows"]:
        lines.append("")
        lines.append("REJECTED ROWS (details in rejected_records.txt)")
        lines.append(format_rejections(result["data_quality"]))

    lines.append("")
    lines.append("MEASUREMENT SUMMARIES")
    summaries = result["summaries"]
    lines.append(format_measurement("heart rate", summaries["heart_rate"], "bpm"))
    lines.append(format_measurement("skin response", summaries["skin_response"]))
    lines.append(format_measurement("temperature", summaries["temperature"], "C"))
    lines.append(format_measurement("activity level", summaries["activity_level"]))

    lines.append("")
    lines.append("COMPARED WITH PERSONAL BASELINE")
    if comparison["heart_rate_delta"] is None:
        lines.append("  no usable data to compare")
    else:
        lines.append(f"  heart rate:     {comparison['heart_rate_delta']:+.2f} bpm")
        lines.append(f"  skin response:  {comparison['skin_response_delta']:+.2f}")
        lines.append(f"  temperature:    {comparison['temperature_delta']:+.2f} C")
        lines.append(f"  activity level: {comparison['average_activity_level']:.2f}")

    lines.append("")
    lines.append("RECOVERY CHECK")
    if recovery["heart_rate_drop"] is None:
        lines.append(f"  {recovery['reason']}")
    else:
        lines.append(f"  recovering: {'yes' if recovery['is_recovering'] else 'no'}")
        lines.append(f"  {recovery['reason']}")

    lines.append("")
    lines.append("CLASSIFICATION")
    lines.append(f"  {classification['label'].upper()}")
    lines.append(f"  {classification['explanation']}")
    lines.append("=" * LINE_WIDTH)

    return "\n".join(lines)


# --- whole run ---------------------------------------------------------------


def format_summary_table(results: Sequence[Dict]) -> str:
    """A compact overview of how every session was classified."""
    lines = [
        f"{'session':<15}{'participant':<13}{'usable':<9}{'classification'}",
        "-" * LINE_WIDTH,
    ]
    for result in results:
        quality = result["data_quality"]
        usable = f"{quality['usable_observations']}/{quality['total_observations']}"
        lines.append(
            f"{result['session_id']:<15}"
            f"{result['participant']['participant_id']:<13}"
            f"{usable:<9}"
            f"{result['classification']['label']}"
        )
    return "\n".join(lines)


def build_analysis_report(results: Sequence[Dict]) -> str:
    """The full analysis_report.txt: an overview followed by every session."""
    lines = [
        "SMART FITNESS SESSION ANALYSIS REPORT",
        f"sessions analysed: {len(results)}",
        "",
        "OVERVIEW",
        format_summary_table(results) if results else "  no sessions could be analysed",
        "",
    ]
    for result in results:
        lines.append(build_report(result))
        lines.append("")
    return "\n".join(lines)


def format_rejected_record(record: RejectedRecord) -> List[str]:
    lines = [f"{record.source_file}, row {record.row_number}"]
    for field_name, reason in record.issues:
        lines.append(f"  field {field_name}: {reason}")
    if record.raw_text:
        lines.append(f"  data: {record.raw_text}")
    return lines


def build_rejected_report(
    load_results: Sequence[LoadResult],
    file_errors: Sequence[str],
    unlinked_sessions: Sequence[str] = (),
) -> str:
    """The full rejected_records.txt."""
    total_rejected = sum(r.rows_rejected for r in load_results)
    lines = [
        "REJECTED RECORDS",
        "=" * LINE_WIDTH,
        f"rows rejected in total: {total_rejected}",
        "",
    ]

    for load_result in load_results:
        lines.append(
            f"--- {load_result.source_file}: {load_result.rows_rejected} rejected "
            f"of {load_result.rows_read} rows read ---"
        )
        if not load_result.rejected:
            lines.append("none")
        for record in load_result.rejected:
            lines.extend(format_rejected_record(record))
            lines.append("")
        lines.append("")

    if unlinked_sessions:
        lines.append("--- sessions not analysed ---")
        lines.append(
            "These sessions never named a valid, known participant, so there is no "
            "baseline to compare them with:"
        )
        for session_id in unlinked_sessions:
            lines.append(f"  {session_id}")
        lines.append("")

    if file_errors:
        lines.append("--- files that could not be read ---")
        for message in file_errors:
            lines.append(f"  {message}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
