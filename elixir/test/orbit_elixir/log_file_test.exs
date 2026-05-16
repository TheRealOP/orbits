defmodule OrbitElixir.LogFileTest do
  use ExUnit.Case, async: true

  alias OrbitElixir.LogFile

  test "default_log_file/0 uses the current working directory" do
    assert LogFile.default_log_file() == Path.join(File.cwd!(), "log/orbit.log")
  end

  test "default_log_file/1 builds the log path under a custom root" do
    assert LogFile.default_log_file("/tmp/orbit-logs") == "/tmp/orbit-logs/log/orbit.log"
  end
end
