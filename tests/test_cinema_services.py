import tempfile
import unittest
from pathlib import Path

from backend.app import database
from backend.app.security import hash_password, verify_password
from backend.app.services.cinema import seat_index, seat_name, validate_showtime


class CinemaServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp_dir.name) / "cinema-test.db"
        database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_seeded_rooms_have_fixed_capacities(self):
        rooms = database.list_rooms()
        self.assertEqual(
            [(room["name"], room["capacity"]) for room in rooms],
            [("Phòng 01", 60), ("Phòng 02", 80), ("Phòng VIP", 40)],
        )

    def test_seat_identifiers_cover_room_capacity(self):
        self.assertEqual(seat_name(60, 60), "E12")
        self.assertEqual(seat_index("E12", 60), 60)
        self.assertIsNone(seat_index("E13", 60))
        self.assertIsNone(seat_index("A001", 60))
        self.assertEqual(seat_name(80, 80), "E16")
        self.assertEqual(seat_index("E16", 80), 80)

    def test_seat_status_is_scoped_to_showtime(self):
        showtimes = database.list_showtimes()
        first_status = database.showtime_seat_status(showtimes[0]["id"])
        second_status = database.showtime_seat_status(showtimes[1]["id"])
        self.assertEqual(first_status, {"capacity": 60, "sold": ["A01", "A02"]})
        self.assertEqual(second_status, {"capacity": 80, "sold": []})

    def test_showtime_validation_rejects_overlapping_schedule(self):
        connection = database.get_connection()
        try:
            movie_ids = [
                row["id"] for row in
                connection.execute("SELECT id FROM movies ORDER BY id LIMIT 2")
            ]
            room_id = connection.execute(
                "SELECT id FROM rooms ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO showtimes(movie_id,room_id,show_date,show_time,price)
                   VALUES(?,?,?,?,?)""",
                (movie_ids[0], room_id, "2099-01-01", "10:00", 80000),
            )
            with self.assertRaisesRegex(ValueError, "trùng thời gian"):
                validate_showtime(
                    connection,
                    movie_ids[1],
                    room_id,
                    "2099-01-01",
                    "11:00",
                    80000,
                )
        finally:
            connection.close()

    def test_showtime_validation_accepts_thousand_vnd_price(self):
        connection = database.get_connection()
        try:
            movie_id = connection.execute(
                "SELECT id FROM movies ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            room_id = connection.execute(
                "SELECT id FROM rooms ORDER BY id LIMIT 1"
            ).fetchone()["id"]

            validate_showtime(
                connection, movie_id, room_id, "2099-01-01", "10:00", 71000
            )
        finally:
            connection.close()

    def test_showtime_validation_rejects_price_not_rounded_to_thousand(self):
        connection = database.get_connection()
        try:
            movie_id = connection.execute(
                "SELECT id FROM movies ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            room_id = connection.execute(
                "SELECT id FROM rooms ORDER BY id LIMIT 1"
            ).fetchone()["id"]

            with self.assertRaisesRegex(ValueError, "bội số của 1.000"):
                validate_showtime(
                    connection, movie_id, room_id, "2099-01-01", "10:00", 71001
                )
        finally:
            connection.close()

    def test_password_hash_verification(self):
        stored = hash_password("demo-password")
        self.assertTrue(verify_password("demo-password", stored))
        self.assertFalse(verify_password("incorrect", stored))


if __name__ == "__main__":
    unittest.main()
