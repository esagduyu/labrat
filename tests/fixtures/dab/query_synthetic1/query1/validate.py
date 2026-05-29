def validate(llm_output: str) -> tuple[bool, str]:
    return ("3" in llm_output, "expected '3' in output")
