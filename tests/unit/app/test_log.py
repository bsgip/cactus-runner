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


def test_read_log_file_tail_returns_recent_lines(tmp_path_factory):
    """tail_bytes should return only the end of the file, dropping the partial first line"""
    lines = [f"line{i}" for i in range(100)]
    log_data = "\n".join(lines) + "\n"

    log_file_path = tmp_path_factory.mktemp("log") / "test.log"
    log_file_path.write_text(log_data)

    # Request only enough bytes to cover the last ~10 lines
    tail_bytes = sum(len(f"line{i}\n") for i in range(90, 100))
    result = read_log_file(log_file_path, tail_bytes=tail_bytes)

    assert isinstance(result, str)
    # The most recent lines are present
    for i in range(91, 100):  # line90 may be partial and dropped
        assert f"line{i}" in result
    # Early lines are not present
    assert "line0" not in result
    assert "line50" not in result


def test_read_log_file_tail_larger_than_file(tmp_path_factory):
    """When tail_bytes exceeds file size, the full content is returned"""
    log_data = "line1\nline2\nline3\n"

    log_file_path = tmp_path_factory.mktemp("log") / "test.log"
    log_file_path.write_text(log_data)

    result = read_log_file(log_file_path, tail_bytes=1024 * 1024)

    assert result == log_data


def test_read_log_file_tail_dne(tmp_path_factory):
    """Ensure tail mode doesn't crash on missing file"""
    log_file_path = tmp_path_factory.mktemp("log") / "test.log"

    result = read_log_file(log_file_path, tail_bytes=64 * 1024)
    assert isinstance(result, str)
