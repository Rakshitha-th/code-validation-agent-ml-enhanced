from app.services.ollama_service import generate_code


async def validate_fix(
    code: str,
    finding: str,
    severity: str
) -> dict:

    prompt = f"""
You are a code validation assistant.

The original code had the following finding:

FINDING:
{finding}

SEVERITY:
{severity}

The code has now been fixed.

FIXED CODE:
{code}

Determine whether the original finding has been resolved.

Return exactly this format:

STATUS:
<RESOLVED or NOT_RESOLVED>

EXPLANATION:
<explain why the original finding is resolved or still present>

REMAINING_ISSUE:
<describe the remaining issue, or write NONE>
"""

    response = await generate_code(prompt)

    status = "UNKNOWN"
    explanation = ""
    remaining_issue = ""

    sections = response.split("EXPLANATION:")

    if len(sections) == 2:
        status_part = sections[0]

        if "RESOLVED" in status_part and "NOT_RESOLVED" not in status_part:
            status = "RESOLVED"
        elif "NOT_RESOLVED" in status_part:
            status = "NOT_RESOLVED"

        remaining_parts = sections[1].split("REMAINING_ISSUE:")

        explanation = remaining_parts[0].strip()

        if len(remaining_parts) == 2:
            remaining_issue = remaining_parts[1].strip()

    return {
        "status": status,
        "explanation": explanation,
        "remaining_issue": remaining_issue
    }