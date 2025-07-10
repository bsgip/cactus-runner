from cactus_runner.app.log import read_log_file


def test_read_log_file(tmp_path_factory):
    """Check we can read an existing file from disk and maintain all of its data"""
    log_data = "abc\n123\n\n456\n\n"

    log_file_path = tmp_path_factory.mktemp("log") / "test.log"

    with open(log_file_path, "w") as file:
        file.write(log_data)

    result = read_log_file(log_file_path)
    assert isinstance(result, str)
    assert result == log_data


def test_read_log_file_dne(tmp_path_factory):
    """Ensure we don't crash"""
    log_file_path = tmp_path_factory.mktemp("log") / "test.log"

    result = read_log_file(log_file_path)
    assert isinstance(result, str)
