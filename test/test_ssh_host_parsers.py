from backend.pollers.ssh_host import (
    _parse_throttle, parse_apps, parse_df, parse_diskstats, parse_gpu,
    parse_loadavg, parse_meminfo, parse_models, parse_netdev, parse_proc,
    parse_ps, parse_ps_ticks, parse_service, parse_smi_cuda, parse_stat,
    parse_uptime, split_sections,
)

GPU_LINE = ("0, NVIDIA GeForce RTX 3080, 550.54.15, 10240, 8000, 2240, 95, 80, "
            "72, 250.5, 350.0, 45, 1440, 9750, 3, 11, P0, 80, 100, 0, Not Active")


def test_split_sections():
    out = split_sections("==A==\nx1\n==B==\nx2\nx3\n")
    assert out == {"A": "x1", "B": "x2\nx3"}


def test_parse_gpu():
    gpus = parse_gpu(GPU_LINE)
    assert len(gpus) == 1
    g = gpus[0]
    assert g["index"] == 0
    assert g["name"] == "NVIDIA GeForce RTX 3080"
    assert g["mem_total_mb"] == 10240
    assert g["mem_used_mb"] == 8000
    assert g["util_pct"] == 95.0
    assert g["temp_c"] == 72.0
    assert g["power_w"] == 250.5
    assert g["fan_pct"] == 45
    assert g["throttle"] == 0
    assert g["apps"] == []
    assert parse_gpu("") == []
    assert parse_gpu("short, line") == []


def test_parse_throttle():
    assert _parse_throttle("Not Active") == 0
    assert _parse_throttle("0x0000000000000001") == 1
    assert _parse_throttle("3") == 3
    assert _parse_throttle("") == 0
    assert _parse_throttle("garbage") == 0


def test_parse_smi_cuda():
    assert parse_smi_cuda("| NVIDIA-SMI 550.54.15   KMD 550.54.15    CUDA Version 12.4 |") == "12.4"
    assert parse_smi_cuda("") is None


def test_parse_apps():
    apps = parse_apps("1234, /usr/bin/llama-server, 8000")
    assert apps == [{"pid": 1234, "name": "llama-server", "mem_mb": 8000}]


def test_parse_stat():
    stat = parse_stat("cpu  1000 0 500 5000 100 0 50 0 0 0\ncpu0 500 0 250 2500 50 0 25 0 0 0\nintr 123")
    assert stat["total"] == 6650
    assert stat["idle"] == 5100  # idle + iowait
    assert stat["cores"] == {0: (3325, 2550)}


def test_parse_meminfo():
    mem = parse_meminfo(
        "MemTotal:       32768000 kB\nMemFree:         2048000 kB\n"
        "MemAvailable:   16384000 kB\nBuffers:          512000 kB\n"
        "Cached:          8192000 kB\nSwapTotal:       4096000 kB\nSwapFree:        4096000 kB\n")
    assert mem["total_mb"] == 32000
    assert mem["used_mb"] == 21500
    assert mem["swap_used_mb"] == 0


def test_parse_loadavg_uptime():
    assert parse_loadavg("0.52 1.01 1.76 2/310 12345") == [0.52, 1.01, 1.76]
    assert parse_uptime("123456.78 999999.0") == 123456.78
    assert parse_uptime("") is None


def test_parse_netdev_excludes_virtual():
    net = parse_netdev(
        "Inter-|   Receive\n"
        "  eth0: 123456789 0 0 0 0 0 0 0 987654321 0 0 0 0 0 0 0 0 0\n"
        "    lo: 1000 0 0 0 0 0 0 0 1000 0 0 0 0 0 0 0 0 0\n"
        " docker0: 500 0 0 0 0 0 0 0 600 0 0 0 0 0 0 0 0 0\n")
    assert [i["name"] for i in net] == ["eth0"]
    assert net[0]["rx_bytes"] == 123456789
    assert net[0]["tx_bytes"] == 987654321


def test_parse_diskstats():
    d = parse_diskstats(
        " 259       0 nvme0n1 1000 0 50000 200 2000 0 80000 400 0 600 0\n"
        "   8       0 sda 100 0 5000 20 2000 0 8000 40 0 60 0 0 0\n")
    assert d == {"sectors_read": 55000, "sectors_written": 88000}


def test_parse_df():
    mounts = parse_df(
        "Filesystem     1B-blocks       Used  Available Use% Mounted on\n"
        "/dev/nvme0n1p2 / 100000000000 50000000000 50000000000  50%\n")
    assert len(mounts) == 1
    assert mounts[0]["mount"] == "/"
    assert mounts[0]["use_pct"] == 50.0
    assert abs(mounts[0]["size_gb"] - 93.1) < 0.1


def test_parse_proc():
    from backend.diff import DiffEngine
    diff = DiffEngine()
    diff.process_cpu_pct("proc:h:1234", 1.0, 0)  # 建立基线
    proc = parse_proc(
        "P:1234\n100 50 2000000\nVmRSS:\t 102400 kB\nVmSize:\t 2048000 kB\n"
        "Threads:\t32\n12.5 3.2 01:02:03\n/usr/bin/llama-server -m /models/qwen.gguf\n",
        diff, 2.0, "h")
    assert proc["found"] is True
    assert proc["pid"] == 1234
    assert proc["rss_mb"] == 100
    assert proc["vsz_mb"] == 2000
    assert proc["threads"] == 32
    assert proc["cpu_pct_lifetime"] == 12.5
    assert proc["elapsed"] == "01:02:03"
    assert proc["cmdline"] == "/usr/bin/llama-server -m /models/qwen.gguf"
    assert parse_proc("", diff, 2.0, "h") == {"found": False}


def test_parse_ps_with_space_name():
    procs = parse_ps("1234 llama-server 12.5 3.2 102400\n5678 my proc 1.0 0.5 51200")
    assert procs[0] == {"pid": 1234, "name": "llama-server", "cpu_pct_lifetime": 12.5,
                        "mem_pct": 3.2, "rss_mb": 100}
    assert procs[1]["name"] == "my proc"


def test_parse_ps_ticks():
    ticks = parse_ps_ticks("1234 100 50\n5678 10 5\nbad line")
    assert ticks == {1234: 150, 5678: 15}


def test_parse_service():
    svc = parse_service(
        "Description=llama server\nActiveState=active\nSubState=running\n"
        "CPUUsageNSec=123456789012345\nMemoryCurrent=1073741824\n"
        "MemoryPeak=2147483648\nNTasks=32\n")
    assert svc["active"] == "active (running)"
    assert svc["memory"] == "1.0G"
    assert svc["memory_peak"] == "2.0G"
    assert svc["tasks"] == 32


def test_parse_models():
    m = parse_models("-rw-r--r-- 1 root root 27320000000 Sep  1 10:00 /models/qwen.gguf")
    assert m == {"/models/qwen.gguf": 27320000000}
