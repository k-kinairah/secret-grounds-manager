from dataclasses import dataclass


@dataclass(frozen=True)
class OperatorDefinition:
    operator_id: str
    username: str
    display_name: str
    password_required: bool


FAMILY_OPERATORS: tuple[OperatorDefinition, ...] = (
    OperatorDefinition("operator_mama", "operator_mama", "Mama", False),
    OperatorDefinition("operator_neil", "operator_neil", "Neil", False),
    OperatorDefinition("operator_tyrone", "operator_tyrone", "Tyrone", False),
    OperatorDefinition("operator_rogene", "operator_rogene", "Rogene", False),
    OperatorDefinition("operator_rojan", "operator_rojan", "Rojan", False),
    OperatorDefinition("operator_bel", "operator_bel", "Bel", False),
    OperatorDefinition("operator_karen", "operator_karen", "Karen", False),
    OperatorDefinition("operator_admin", "admin", "Admin", True),
)

FAMILY_OPERATOR_BY_ID = {operator.operator_id: operator for operator in FAMILY_OPERATORS}
FAMILY_OPERATOR_BY_USERNAME = {
    operator.username.casefold(): operator for operator in FAMILY_OPERATORS
}
