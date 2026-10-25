"""HM Land Registry Price Paid Data -> local `sold_prices` table (zero API credits).

Yearly files are streamed and filtered on the fly, so the national data never lands on disk.
Contains HM Land Registry data © Crown copyright and database right. Licensed under the
Open Government Licence v3.0. (Attribution must be kept wherever this data is shown.)
"""
import csv
import re
from datetime import datetime

import requests
from sqlalchemy import delete

from ..db import Session, SoldPrice

# The public S3 website host 301s to prod1; go straight there.
BASE = "http://prod1.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com"
COLS = ["transaction_id", "price", "date", "postcode", "property_type", "new_build", "tenure",
        "paon", "saon", "street", "locality", "town", "district", "county", "ppd_category", "record_status"]
TYPE = {"D": "detached", "S": "semi-detached", "T": "terraced", "F": "flat", "O": "other"}

# Postcode areas within reach of a 15-mile radius of L35; distance is applied per search later.
DEFAULT_AREAS = ("L", "WA", "WN", "CH")


def load_year(year: int, areas=DEFAULT_AREAS) -> int:
    pattern = re.compile(rf"^({'|'.join(areas)})\d")
    with requests.get(f"{BASE}/pp-{year}.csv", stream=True, timeout=120) as r:
        r.raise_for_status()
        rows = []
        r.encoding = "utf-8"
        for rec in csv.reader(r.iter_lines(decode_unicode=True)):
            row = dict(zip(COLS, rec))
            if not pattern.match(row["postcode"]):
                continue
            rows.append(SoldPrice(
                transaction_id=row["transaction_id"].strip("{}"), price=int(row["price"]),
                sale_date=datetime.strptime(row["date"][:10], "%Y-%m-%d").date(),
                postcode=row["postcode"], outcode=row["postcode"].split()[0],
                property_type=TYPE.get(row["property_type"], "other"), new_build=row["new_build"] == "Y",
                tenure=row["tenure"], paon=row["paon"], saon=row["saon"], street=row["street"],
                locality=row["locality"], town=row["town"], district=row["district"],
                ppd_category=row["ppd_category"], source=f"pp-{year}"))
    with Session.begin() as s:  # replace the year wholesale: yearly files are full snapshots
        s.execute(delete(SoldPrice).where(SoldPrice.source == f"pp-{year}"))
        s.add_all(rows)
    return len(rows)
