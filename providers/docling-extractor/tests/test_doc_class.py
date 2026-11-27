"""doc_class rules — pinned against the operator-confirmed 19-doc sample.

The fixtures below are content snippets in the shape of the real corpus
(issuer headers, marker phrases) — the assertions pin the CONFIRMED
classes, content-only (labels are never consulted), per the operator's
rulings: filenames are never a signal; mixed/unknown route to Triage.
"""

from doc_class import classify


def _dbls_statement_head(month: str) -> str:
    return (
        "NG JUN SIANG (HUANG JUNXIANG) BLK 239 HOUGANG STREET 22 SINGAPORE\n"
        "## Account Summary as at "
        f"{month}\n\n"
        "## Deposits\n\n## Current and Savings Account\n\n"
        "DBS\n\nPOSB\n\n## Consolidated Statement\n\n"
        "Total: SGD Equivalent 672.35\n\n"
        "## Transaction Details as at "
        f"{month}\n\n"
        "## Supplementary Retirement Scheme\n"
        "| Supplementary Retirement Scheme Account - Date "
        "| Description | Balance (SGD) |\n"
        "|---|---|---|\n"
    )


class TestConfirmedSample:
    def test_summary_plus_transaction_table_is_mixed_to_triage(self):
        """The DBS-style shape: summary sections AND transaction tables in
        one document — the edge case the operator ruled to Triage."""
        r = classify(_dbls_statement_head("30 Jun 2025"))
        assert r["doc_class"] == "mixed"
        assert r["mixed"] is True
        assert r["confidence"] == "low"

    def test_transaction_table_without_summary_markers_is_statement(self):
        r = classify(
            "## Debit Cards\n\nfor ACCOUNT: 002-38692-5\n\nAs at 09 Jul 2025\n\n"
            "| DATE | DESCRIPTION - TRANSACTION DATE | AMOUNT(S$) |\n"
            "| 10 Jun | EZ-LINK MOTORING SINGAPORE SGP 08 JUN | $ 3.92 |\n"
            "| 24 Jun | SIMPLYGO SINGAPORE SGP 22 JUN | $ 50.00 |\n"
        )
        assert r["doc_class"] == "statement"
        assert r["confidence"] == "medium"   # one marker matched
        assert r["mixed"] is False

    def test_summary_only_statement_head_is_overview(self):
        r = classify(
            "S/N: EN05011103474341\n\nLUCA NG 37 PUNGGOL FIELD SINGAPORE\n\n"
            "## Account Summary as at 31 Oct 2026\n\nDeposits\n\n"
            "## Current and Savings Account\n\nSummary of Currency Breakdown:\n"
            "| Account | Account No. | Balance (Base Currency) |\n"
            "| My Account | 046-208683-5 | SGD 1.00 |\n\n"
            "DBS\n\nPOSB\n\n## Consolidated Statement\n"
        )
        assert r["doc_class"] == "overview"
        assert r["mixed"] is False

    def test_uob_credit_card_statement_is_statement(self):
        r = classify(
            "UOB\n\nMR NG JUN SIANG\n\n## Credit Card(s) Statement\n\n"
            "| Summary Card Name | Card Number | Total Amount | Minimum Payment |\n"
            "| UOB ONE CARD | 4265-8840-3569-5724 | Due SGD 779.60 | Due SGD 50.00 |\n\n"
            "## UOB ONE CARD\n"
            "| Post Date | Trans Date | Description of Transaction "
            "| Transaction Amount SGD |\n"
            "| 15 OCT | 15 OCT | PREVIOUS BALANCE | 1,635.06 |\n"
        )
        assert r["doc_class"] == "statement"
        assert r["mixed"] is False

    def test_senoko_tax_invoice_is_bill(self):
        r = classify(
            "Senoko\n\n## ELECTRICITY TAX INVOICE\n\n"
            "MR NG JUN SIANG 37 PUNGGOL FIELD SINGAPORE\n\n"
            "## BILL OCTOBER 2026\n\nTOTAL DUE (SGD) $82.02\n\n"
            "Invoice No. INV0006714982\n\nBilling Period 14 Sep 2026 to 1 Oct 2026\n"
            "| SUMMARY OF CHARGES | Amount (SGD) |\n"
        )
        assert r["doc_class"] == "bill"
        assert r["confidence"] == "high"

    def test_sp_services_utility_bill_is_bill(self):
        r = classify(
            "MR NG JUN SIANG\n\n## September 2026 Bill\n\n"
            "Account No. 8957965646\n\nTotal Amount Payable: $150.43\n\n"
            "Payment Due: 18 Sep 2026\n\nSP Services Ltd\n"
        )
        assert r["doc_class"] == "bill"
        assert r["confidence"] == "high"

    def test_uob_cashline_is_overview(self):
        r = classify(
            "## Cashline\n\nStatement of Account\n\n"
            "CASHLINE ACCOUNT NO 082-489134-0\n\n"
            "CREDIT LIMIT S$5,750.00\n\nAVAILABLE BALANCE S$5,750.00\n\n"
            "Total Outstanding Balance S$10.00\n"
        )
        assert r["doc_class"] == "overview"
        assert r["confidence"] == "high"

    def test_aia_change_of_plan_is_notice(self):
        r = classify(
            "Oct 23, 2026\n\nDear Ng Jun Siang (Huang Junxiang),\n\n"
            "Approval of Change of Plan request with Premium Notice enclosed.\n"
        )
        assert r["doc_class"] == "notice"

    def test_aia_policy_contract_is_policy(self):
        r = classify(
            "Dear Policy Owner\n\n## Policy No. : E234301588\n\n"
            "your Policy has been changed to AIA MAX VITALHEALTH PRO B\n\n"
            "Enclosed is the new Policy contract and an Endorsement.\n\n"
            "Should you decide within the Freelook period\n"
        )
        assert r["doc_class"] == "policy"
        assert r["confidence"] == "high"

    def test_commemorative_book_is_document(self):
        r = classify(
            "## THE FIRST 20 YEARS\n\n## THE STORY OF CSS\n\n"
            "## Message by Founding Commander Combat Service Support Command\n\n"
            "## Foreword by Commander Combat Service Support Command\n"
        )
        assert r["doc_class"] == "document"

    def test_no_text_scan_is_unknown(self):
        r = classify("FRONT\n\n<!-- image -->")
        assert r["doc_class"] == "unknown"
        assert r["confidence"] == "low"


class TestMixedAndEdges:
    def test_statement_and_overview_markers_copresent_is_mixed(self):
        """The One Account / COMBINE shape: overview-dominant body with an
        embedded transaction-details table — the edge case that routes to
        Triage pending the single-class vs tags decision."""
        r = classify(
            "UOB\n\nNG JUN SIANG\n\n## Statement of Account\n\n"
            "Period: 01 Nov 2022 to 30 Nov 2022\n\n"
            "| Account Overview as at 30 Nov 2022 | (SGD) |\n"
            "| Deposits | 964.46 |\n\n"
            "## ONE Account Interest Overview\n\n"
            "--------- End of Summary------------\n\n"
            "| Account Transaction Details | Description | Amount |\n"
        )
        assert r["doc_class"] == "mixed"
        assert r["mixed"] is True
        assert r["confidence"] == "low"

    def test_mixed_requires_overview_dominance(self):
        # overview + bill co-presence is NOT the ruled mixed pair
        r = classify(
            "## Account Summary as at 31 Oct 2026\n\n"
            "Total Amount Payable: $150.43\n\nPayment Due: 18 Sep 2026\n"
        )
        assert r["doc_class"] == "bill"          # bill outranks, not mixed
        assert r["mixed"] is False

    def test_statement_dominant_copresence_is_statement(self):
        """The UOB CC shape: summary header + a dominant transaction
        table — confirmed statement, not mixed."""
        r = classify(
            "UOB\n\nMR NG JUN SIANG\n\n## Credit Card(s) Statement\n\n"
            "| Post Date | Trans Date | Description of Transaction | Amount |\n"
            "| 15 OCT | 15 OCT | PREVIOUS BALANCE | 1,635.06 |\n"
        )
        assert r["doc_class"] == "statement"
        assert r["mixed"] is False

    def test_no_rule_matched_is_unknown(self):
        r = classify("lorem ipsum dolor sit amet consectetur adipiscing elit "
                     "sed do eiusmod tempor incididunt ut labore et dolore")
        assert r["doc_class"] == "unknown"
        assert "no rule matched" in r["evidence"][0]
