"""Smart Fitness Session Analyzer.

Modules
-------
exceptions  custom exception classes
validators  regular-expression identifier checks
models      domain classes (profile, observations, session, rejected record)
loader      CSV reading, typing, row validation and grouping into sessions
analysis    summaries, baseline comparison, recovery detection, classification
reporting   turns results into text
writers     writes the output files
cli         command line front end
"""
