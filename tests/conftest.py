import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def chunks():
    output_path = Path("data/processed/vic_rta_chunks.json")
    if not output_path.exists():
        pytest.skip("Output file not found — run parser.py first")
    with open(output_path, encoding="utf-8") as f:
        return json.load(f)


# ── vector_store fixtures ─────────────────────────────────────────────


@pytest.fixture
def sample_chunks():
    return [
        {
            "chunk_id": "VIC-RTA1997-s44",
            "text": (
                "[Part 2 - Division 3] 44 Rent increases\n"
                "(1) A residential rental provider must give a renter "
                "at least 90 days notice of a proposed rent increase."
            ),
            "state": "VIC",
            "act": "Residential Tenancies Act 1997",
            "year": "1997",
            "section_id": "44",
            "section_title": "Rent increases",
            "part": "2",
            "part_title": "Residential tenancies",
            "division": "3",
            "division_title": "Rents",
            "subdivision": None,
            "subdivision_title": None,
            "subsection_range": None,
        },
        {
            "chunk_id": "VIC-RTA1997-s91ZM",
            "text": (
                "[Part 2 - Division 9 - Subdivision 5] 91ZM Non-payment of rent\n"
                "(1) A residential rental provider may give a renter a notice "
                "to vacate rented premises if the renter is at least 14 days "
                "in arrears in the payment of rent."
            ),
            "state": "VIC",
            "act": "Residential Tenancies Act 1997",
            "year": "1997",
            "section_id": "91ZM",
            "section_title": "Non-payment of rent",
            "part": "2",
            "part_title": "Residential tenancies",
            "division": "9",
            "division_title": "Termination of residential rental agreements",
            "subdivision": "5",
            "subdivision_title": "Notice by residential rental provider",
            "subsection_range": None,
        },
        {
            "chunk_id": "VIC-RTA1997-s91ZM",
            "text": (
                "[Part 2 - Division 9 - Subdivision 5] 91ZM Non-payment of rent\n"
                "(2) The notice must specify a date that is not less than "
                "14 days after the day on which the notice is given."
            ),
            "state": "VIC",
            "act": "Residential Tenancies Act 1997",
            "year": "1997",
            "section_id": "91ZM",
            "section_title": "Non-payment of rent",
            "part": "2",
            "part_title": "Residential tenancies",
            "division": "9",
            "division_title": "Termination of residential rental agreements",
            "subdivision": "5",
            "subdivision_title": "Notice by residential rental provider",
            "subsection_range": "91ZM(2)-91ZM(2)",
        },
        {
            "chunk_id": "NSW-RTA2010-s44",
            "text": (
                "[Part 3 - Division 2] 44 Rent increases\n"
                "(1) A landlord must give the tenant at least 60 days "
                "written notice of a rent increase."
            ),
            "state": "NSW",
            "act": "Residential Tenancies Act 2010",
            "year": "2010",
            "section_id": "44",
            "section_title": "Rent increases",
            "part": "3",
            "part_title": "Rights and obligations",
            "division": "2",
            "division_title": "Rent",
            "subdivision": None,
            "subdivision_title": None,
            "subsection_range": None,
        },
        {
            "chunk_id": "VIC-RTA1997-s213",
            "text": (
                "[Part 5] 213 Compensation for unpaid rent\n"
                "(1) A residential rental provider is not entitled to "
                "claim compensation for a failure of a renter to pay rent."
            ),
            "state": "VIC",
            "act": "Residential Tenancies Act 1997",
            "year": "1997",
            "section_id": "213",
            "section_title": "Compensation for unpaid rent",
            "part": "5",
            "part_title": "Compensation and compliance",
            "division": None,
            "division_title": None,
            "subdivision": None,
            "subdivision_title": None,
            "subsection_range": None,
        },
    ]


@pytest.fixture
def sample_chunks_file(sample_chunks, tmp_path):
    path = tmp_path / "sample_chunks.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sample_chunks, f)
    return str(path)


@pytest.fixture
def empty_chunks_file(tmp_path):
    path = tmp_path / "empty.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump([], f)
    return str(path)


@pytest.fixture(scope="module")
def qdrant_with_data():
    from src.retrieval import vector_store as vs

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_path = vs.QDRANT_PATH
        orig_collection = vs.COLLECTION_NAME
        vs.QDRANT_PATH = tmpdir
        vs.COLLECTION_NAME = "test_tenancy_acts"

        sample = [
            {
                "chunk_id": "VIC-RTA1997-s44",
                "text": "[Part 2] 44 Rent increases\n(1) A provider must give 90 days notice of a proposed rent increase.",
                "state": "VIC",
                "act": "Residential Tenancies Act 1997",
                "year": "1997",
                "section_id": "44",
                "section_title": "Rent increases",
                "part": "2",
                "part_title": "Residential tenancies",
                "division": "3",
                "division_title": "Rents",
                "subdivision": None,
                "subdivision_title": None,
                "subsection_range": None,
            },
            {
                "chunk_id": "VIC-RTA1997-s91ZM",
                "text": "[Part 2] 91ZM Non-payment of rent\n(1) A provider may give a 14 day notice to vacate for non-payment of rent.",
                "state": "VIC",
                "act": "Residential Tenancies Act 1997",
                "year": "1997",
                "section_id": "91ZM",
                "section_title": "Non-payment of rent",
                "part": "2",
                "part_title": "Residential tenancies",
                "division": "9",
                "division_title": "Termination",
                "subdivision": "5",
                "subdivision_title": "Notice by provider",
                "subsection_range": None,
            },
            {
                "chunk_id": "VIC-RTA1997-s91ZM",
                "text": "[Part 2] 91ZM Non-payment of rent\n(2) The notice must specify not less than 14 days after the notice is given.",
                "state": "VIC",
                "act": "Residential Tenancies Act 1997",
                "year": "1997",
                "section_id": "91ZM",
                "section_title": "Non-payment of rent",
                "part": "2",
                "part_title": "Residential tenancies",
                "division": "9",
                "division_title": "Termination",
                "subdivision": "5",
                "subdivision_title": "Notice by provider",
                "subsection_range": "91ZM(2)-91ZM(2)",
            },
            {
                "chunk_id": "NSW-RTA2010-s44",
                "text": "[Part 3] 44 Rent increases\n(1) A landlord must give 60 days written notice of a rent increase.",
                "state": "NSW",
                "act": "Residential Tenancies Act 2010",
                "year": "2010",
                "section_id": "44",
                "section_title": "Rent increases",
                "part": "3",
                "part_title": "Rights and obligations",
                "division": "2",
                "division_title": "Rent",
                "subdivision": None,
                "subdivision_title": None,
                "subsection_range": None,
            },
            {
                "chunk_id": "VIC-RTA1997-s213",
                "text": "[Part 5] 213 Compensation for unpaid rent\n(1) A provider may claim compensation for unpaid rent.",
                "state": "VIC",
                "act": "Residential Tenancies Act 1997",
                "year": "1997",
                "section_id": "213",
                "section_title": "Compensation for unpaid rent",
                "part": "5",
                "part_title": "Compensation and compliance",
                "division": None,
                "division_title": None,
                "subdivision": None,
                "subdivision_title": None,
                "subsection_range": None,
            },
        ]

        sample_file = Path(tmpdir) / "sample.json"
        with open(sample_file, "w", encoding="utf-8") as f:
            json.dump(sample, f)

        count = vs.ingest_chunks_to_qdrant(str(sample_file))

        data = {
            "count": count,
            "collection": vs.COLLECTION_NAME,
            "path": tmpdir,
        }
        yield data

        vs.QDRANT_PATH = orig_path
        vs.COLLECTION_NAME = orig_collection
