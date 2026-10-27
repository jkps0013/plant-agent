PERMITTED_ACTIONS = [
    "inspect",
    "schedule",
    "notify",
    "check",
    "verify",
    "monitor",
    "investigate",
]

PROHIBITED_ACTIONS = [
    "adjust setpoint",
    "disable interlock",
    "disable safety",
    "bypass",
]

PERMITTED_EQUIPMENT = [
    "Reactor2",
    "Reactor 2",
    "P-203",
    "P203",
    "CC-01",
    "CC01",
]

MAX_URGENCY_HOURS = 24


def check_action(action_text: str) -> tuple[bool, str]:
    action_lower = action_text.lower()

    for prohibited in PROHIBITED_ACTIONS:
        if prohibited in action_lower:
            return False, f"Action contains prohibited term: '{prohibited}'"

    if not any(permitted in action_lower for permitted in PERMITTED_ACTIONS):
        return False, "Action does not match any permitted action type"

    return True, "Action permitted"


def check_equipment(action_text: str) -> tuple[bool, str]:
    action_lower = action_text.lower()
    action_norm = action_lower.replace(" ", "").replace("-", "")

    for equip in PERMITTED_EQUIPMENT:
        equip_lower = equip.lower()
        equip_norm = equip_lower.replace(" ", "").replace("-", "")
        if equip_lower in action_lower or equip_norm in action_norm:
            return True, "Equipment permitted"

    return False, "Action does not reference any permitted equipment"
