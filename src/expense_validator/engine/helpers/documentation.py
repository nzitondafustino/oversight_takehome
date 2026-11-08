"""DOC-03 and REG-04 helpers."""

from expense_validator.engine.helpers.common import HelperOutcome
from expense_validator.engine.helpers.context import HelperJob

# DOC-03: the categories each declared expense type covers, and those its test ignores.
TYPE_COVERS = {
    "Meals - Individual": {"food_beverage", "alcohol"},
    "Meals - Team": {"food_beverage", "alcohol"},
    "Client Entertainment": {"food_beverage", "alcohol"},
    "Client Gifts": {"gift", "gift_card", "jewelry"},
    "Lodging": {"lodging", "ground_transport", "laundry_service"},
    "Travel - Other": {"laundry_service", "health_otc", "ground_transport", "clothing", "personal_care"},
    "Office Supplies": {"office_supplies", "computer_accessory"},
    "Computer Equipment": {"computer_accessory", "personal_electronics", "software_subscription"},
    "Software": {"software_subscription"},
}
TYPE_IGNORES = {"service_fee", "other"}


def _expense_type_mismatch(job: HelperJob) -> HelperOutcome:
    receipt = job.receipt
    covers = TYPE_COVERS.get(receipt.submitted_expense_type)
    if covers is None:
        return job.not_known("the policy table has no entry for this expense type")
    categories = {line.category for line in receipt.line_items if job.category_known(line)}
    if categories & covers:
        return HelperOutcome()
    if len(categories) < len({line.category for line in receipt.line_items}):
        return job.not_known("a line that could match the declared type has no known category")
    if not categories - TYPE_IGNORES:
        return HelperOutcome()
    return job.receipt_finding(f"{job.rule.title}: declared as {receipt.submitted_expense_type}", categories=", ".join(sorted(categories)))


def _vat_threshold(job: HelperJob) -> HelperOutcome:
    """REG-04: the data holds no VAT number, which the rule treats as missing."""
    receipt = job.receipt
    above = job.param("above_eur")
    if receipt.currency == "EUR":
        if receipt.total_amount is None:
            return job.not_known("the receipt has no total")
        is_above = receipt.total_amount > above
    else:
        rate = job.euro_rate()
        if receipt.total_amount_usd is None or rate is None:
            return job.not_known("the total in euros cannot be worked out")
        is_above = receipt.total_amount_usd > above * rate
    if not is_above:
        return HelperOutcome()
    return job.receipt_finding(f"{job.rule.title}: above EUR {above.normalize():f} with no VAT number recorded", vat_number=None)
