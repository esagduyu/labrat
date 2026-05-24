"""DSPy module for Spider2-DBT dbt model completion."""

from __future__ import annotations

import dspy


class DBTCompletion(dspy.Signature):
    """Complete ONLY the model named in `target_file`. Do not write SQL for any other model.

    Study `project_files` to understand the schema, existing models, and CTE patterns,
    then produce complete, valid DuckDB-dialect SQL for that one target model.

    Rules — table references:
    - Use {{ source('schema', 'table') }} for raw source tables.
    - Use {{ ref('model_name') }} for references to other dbt models.
    - Only use macros that are explicitly defined or called in project_files.
      Never invent macros from dbt packages (no fivetran_utils, dbt_utils, etc.)
      unless you see them already used in the project files.

    Rules — DuckDB dialect:
    - Date/time: use current_timestamp (not get_current_timestamp()), strptime() (not
      try_strptime()), TRY_CAST(strptime(x, fmt) AS DATE) for safe date parsing.
    - String aggregation: LIST_AGG(col, ', ') or STRING_AGG(col, ', '), not fivetran_utils.string_agg.
    - Regex: regexp_replace(col, pattern, replacement) — no flags argument.
    - Casting: CAST(x AS type) — avoid x::type shorthand.

    Rules — output:
    - Output complete, runnable SQL — no pseudocode, no code fences, no explanations.
    - Preserve the CTE style already used in the project.
    - If the instruction describes multiple tables/views, only complete the one
      file identified by target_file.
    - If multiple attribution or aggregation methods are possible, pick the one that
      best matches the instruction's intent and any examples in project_files.
    """

    instruction: str = dspy.InputField(
        desc="Natural-language description of the required data transformation"
    )
    project_files: str = dspy.InputField(
        desc="Contents of all relevant dbt project files (profiles, models, schemas)"
    )
    target_file: str = dspy.InputField(
        desc="Relative path of the model file that must be completed (e.g. models/foo.sql)"
    )
    completed_sql: str = dspy.OutputField(
        desc="The complete dbt SQL for the target file — pure SQL only, no code fences"
    )


class DBTModelCompletion(dspy.Module):
    """DSPy module: takes a Spider2-DBT task, returns completed SQL for the target model."""

    def __init__(self) -> None:
        self.generate = dspy.ChainOfThought(DBTCompletion)

    def forward(
        self,
        instruction: str,
        project_files: str,
        target_file: str,
    ) -> dspy.Prediction:
        return self.generate(
            instruction=instruction,
            project_files=project_files,
            target_file=target_file,
        )
