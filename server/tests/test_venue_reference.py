from app.ingestion.normalizer import VENUE_COORDINATES, VENUE_TIMEZONES


def test_current_mlb_venue_aliases_have_coordinates_and_timezones() -> None:
    names = {
        "Angel Stadium", "Chase Field", "Nationals Park", "Rate Field",
        "Sutter Health Park", "loanDepot park", "UNIQLO Field at Dodger Stadium",
        "Daikin Park", "American Family Field", "Great American Ball Park",
        "Kauffman Stadium", "George M. Steinbrenner Field",
    }
    assert names <= VENUE_COORDINATES.keys()
    assert names <= VENUE_TIMEZONES.keys()
    assert all(-90 <= VENUE_COORDINATES[name][0] <= 90 for name in names)
    assert all(-180 <= VENUE_COORDINATES[name][1] <= 180 for name in names)
