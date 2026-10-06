"""Known inputs with known answers, run by the checker to prove the pipeline works.

Fictional notices. "expected" lists the status each check must produce. Statuses
are what the offline extractor and the rules produce; with Claude the extraction
can differ in confidence, so canaries assert only the decided checks and allow
`needs_human_review` for process facts that the document can't prove.
"""

COMPLETE_NOTICE = (
    "Privacy notice of Example Retail Pvt Ltd. "
    "We collect your name, mobile number, email address and delivery address. "
    "The purpose of collecting this data is to process and deliver your orders. "
    "You can withdraw your consent at any time from Settings, and you can raise a grievance "
    "with our Grievance Officer at grievance@example.in. "
    "If you are not satisfied, you may complain to the Data Protection Board of India."
)

INCOMPLETE_NOTICE = (
    "Privacy notice of Example Games Pvt Ltd. "
    "We collect your name and phone number. "
    "Contact us at hello@example.in with any questions."
)

CANARIES = [
    {
        "name": "complete_notice",
        "payload": {
            "doc_type": "privacy_notice",
            "subject": "canary:complete",
            "text": COMPLETE_NOTICE,
            "facts": {"is_given_before_or_with_consent_request": "yes"},
        },
        "expected": {
            "notice.describes_data": "compliant",
            "notice.rights_exercise": "compliant",
            "notice.board_complaint": "compliant",
            "notice.timing": "compliant",
            "notice.language": "compliant",
        },
        "run_status": ("passed",),
    },
    {
        "name": "incomplete_notice",
        "payload": {
            "doc_type": "privacy_notice",
            "subject": "canary:incomplete",
            "text": INCOMPLETE_NOTICE,
        },
        "expected": {
            "notice.describes_data": "gap",
            "notice.rights_exercise": "gap",
            "notice.board_complaint": "gap",
            "notice.timing": "needs_human_review",
        },
        "run_status": ("passed",),
    },
]
