"""Curated instrument fixture (PLAN.md Decision D1).

No public feed maps ISINs to EIX's §7 guaranteed-volume segments, or gives
us real transaction history to compute an RTS 11 ADNT liquidity band, so
this fixture assigns both by hand — another documented proxy, same as the
price/spread feed.

Real, well-known ISINs are used only for the handful of large-cap names
we're confident about (DAX constituents, two well-known UCITS ETFs).
Every other segment is filled with a clearly-labelled synthetic
placeholder ISIN (`SYN-...`) rather than a guessed-but-plausible real one
— the project's no-fabrication rule applies to instrument identifiers too.

Only the EUR/Xetra domestic instruments (`LIVE_SIM_ISINS`) are used in the
live day-simulation (Decision D2); the foreign-currency synthetic entries
exist so venue/guarantees.py's segment lookup and the §15 FX-recording
path can be unit-tested across all 14 segments without a live foreign
reference feed.

Index membership and liquidity-band assignments are approximate/
illustrative, not sourced from a live constituents or transactions feed.
Starting prices are round, illustrative figures, not live quotes.
"""

from __future__ import annotations

from rfq_mm.core.ticks import ETF_LIQUIDITY_BAND
from rfq_mm.core.types import Instrument, Segment

DAX_INSTRUMENTS: list[Instrument] = [
    Instrument(
        isin="DE0007164600", name="SAP SE", segment=Segment.DAX, currency="EUR", liquidity_band=6
    ),
    Instrument(
        isin="DE0007236101",
        name="Siemens AG",
        segment=Segment.DAX,
        currency="EUR",
        liquidity_band=6,
    ),
    Instrument(
        isin="DE0008404005",
        name="Allianz SE",
        segment=Segment.DAX,
        currency="EUR",
        liquidity_band=6,
    ),
    Instrument(
        isin="DE0005557508",
        name="Deutsche Telekom AG",
        segment=Segment.DAX,
        currency="EUR",
        liquidity_band=6,
    ),
    Instrument(
        isin="DE0005190003", name="BMW AG", segment=Segment.DAX, currency="EUR", liquidity_band=5
    ),
    Instrument(
        isin="DE000BASF111",
        name="BASF SE",
        segment=Segment.DAX,
        currency="EUR",
        liquidity_band=5,
    ),
]

ETF_INSTRUMENTS: list[Instrument] = [
    Instrument(
        isin="DE0005933931",
        name="iShares Core DAX UCITS ETF",
        segment=Segment.ETF_DAX,
        currency="EUR",
        liquidity_band=ETF_LIQUIDITY_BAND,
    ),
    Instrument(
        isin="IE00B4L5Y983",
        name="iShares Core MSCI World UCITS ETF",
        segment=Segment.ETF_OTHER,
        currency="EUR",
        liquidity_band=ETF_LIQUIDITY_BAND,
    ),
]

SYNTHETIC_INSTRUMENTS: list[Instrument] = [
    Instrument(
        isin="SYN-MDAX-01",
        name="(synthetic) MDAX proxy",
        segment=Segment.MDAX,
        currency="EUR",
        liquidity_band=4,
    ),
    Instrument(
        isin="SYN-SDAX-01",
        name="(synthetic) SDAX proxy",
        segment=Segment.SDAX,
        currency="EUR",
        liquidity_band=3,
    ),
    Instrument(
        isin="SYN-TECDAX-01",
        name="(synthetic) TecDAX proxy",
        segment=Segment.TECDAX,
        currency="EUR",
        liquidity_band=4,
    ),
    Instrument(
        isin="SYN-DOMOTHER-01",
        name="(synthetic) domestic-other proxy",
        segment=Segment.DOMESTIC_OTHER,
        currency="EUR",
        liquidity_band=2,
    ),
    Instrument(
        isin="SYN-DJ30-01",
        name="(synthetic) DJ30 proxy",
        segment=Segment.DJ30,
        currency="USD",
        liquidity_band=6,
        fx_rate_to_eur=0.92,
    ),
    Instrument(
        isin="SYN-USOTHER-01",
        name="(synthetic) US-other proxy",
        segment=Segment.US_OTHER,
        currency="USD",
        liquidity_band=4,
        fx_rate_to_eur=0.92,
    ),
    Instrument(
        isin="SYN-ESTX50-01",
        name="(synthetic) EuroStoxx 50 proxy",
        segment=Segment.ESTX50,
        currency="EUR",
        liquidity_band=6,
    ),
    Instrument(
        isin="SYN-FOREIGNOTHER-01",
        name="(synthetic) foreign-other proxy",
        segment=Segment.FOREIGN_OTHER,
        currency="GBP",
        liquidity_band=2,
        fx_rate_to_eur=1.17,
    ),
    Instrument(
        isin="SYN-ETC-01",
        name="(synthetic) ETC proxy",
        segment=Segment.ETC,
        currency="EUR",
        liquidity_band=ETF_LIQUIDITY_BAND,
    ),
    Instrument(
        isin="SYN-ETP-01",
        name="(synthetic) ETP proxy",
        segment=Segment.ETP,
        currency="EUR",
        liquidity_band=ETF_LIQUIDITY_BAND,
    ),
    Instrument(
        isin="SYN-FUND-01",
        name="(synthetic) fund proxy",
        segment=Segment.FUND,
        currency="EUR",
        liquidity_band=3,
    ),
    Instrument(
        isin="SYN-LEV-DAX-01",
        name="(synthetic) leveraged DAX product proxy",
        segment=Segment.DAX,
        currency="EUR",
        liquidity_band=3,
        is_leveraged=True,
    ),
]

ALL_INSTRUMENTS: list[Instrument] = DAX_INSTRUMENTS + ETF_INSTRUMENTS + SYNTHETIC_INSTRUMENTS
BY_ISIN: dict[str, Instrument] = {i.isin: i for i in ALL_INSTRUMENTS}

START_PRICES_EUR: dict[str, float] = {
    "DE0007164600": 220.0,
    "DE0007236101": 180.0,
    "DE0008404005": 300.0,
    "DE0005557508": 25.0,
    "DE0005190003": 95.0,
    "DE000BASF111": 45.0,
    "DE0005933931": 145.0,
    "IE00B4L5Y983": 95.0,
    "SYN-MDAX-01": 60.0,
    "SYN-SDAX-01": 40.0,
    "SYN-TECDAX-01": 30.0,
    "SYN-DOMOTHER-01": 15.0,
    "SYN-DJ30-01": 150.0,
    "SYN-USOTHER-01": 80.0,
    "SYN-ESTX50-01": 50.0,
    "SYN-FOREIGNOTHER-01": 20.0,
    "SYN-ETC-01": 35.0,
    "SYN-ETP-01": 25.0,
    "SYN-FUND-01": 100.0,
    "SYN-LEV-DAX-01": 12.0,
}

# EUR/Xetra domestic instruments used in the live day-simulation (Decision D2).
LIVE_SIM_ISINS: list[str] = [i.isin for i in DAX_INSTRUMENTS + ETF_INSTRUMENTS]
