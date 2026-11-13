import asyncio
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest

import hardware_probe

GIB_IN_KIB = 1024 * 1024
LSCPU = "Architecture: x86_64\nCPU(s): 16\nModel name: AMD Ryzen 7 5800X 8-Core Processor\nCPU max MHz: 4850.1953\n"
DMIDECODE = (
    "Memory Device\n\tSize: No Module Installed\n\tType: Unknown\n\tSpeed: Unknown\n"
    "Memory Device\n\tSize: 16 GB\n\tType: DDR4\n\tSpeed: 3200 MT/s\n"
)


@pytest.fixture
def machine(monkeypatch):
    commands = {}
    files = {}

    async def fake_run(cmd, timeout=5.0):
        reply = commands.get(cmd[0])
        if reply is None:
            return -1, ""
        return reply(cmd) if callable(reply) else reply

    real_open = open

    def fake_open(path, *args, **kwargs):
        if path in files:
            return io.StringIO(files[path])
        if path.startswith("/proc/"):
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)

    def install(system):
        monkeypatch.setattr(hardware_probe, "_SYSTEM", system)
        monkeypatch.setattr(hardware_probe, "_run", fake_run)
        monkeypatch.setattr(hardware_probe, "open", fake_open, raising=False)
        return commands, files
    return install


def nvidia(memory_mib, names):
    def answer(cmd):
        if cmd[1].startswith("--query-gpu=memory.total"):
            return 0, "\n".join(str(m) for m in memory_mib) + "\n"
        return 0, "\n".join(names) + "\n"
    return answer


def probe():
    return asyncio.run(hardware_probe.probe())


def fitting(budget):
    return [name for name, size in hardware_probe.CANDIDATE_MODELS if size <= budget]


def test_a_linux_box_with_an_nvidia_card_is_sized_by_its_vram(machine):
    commands, files = machine("Linux")
    files["/proc/meminfo"] = f"MemTotal:       {32 * GIB_IN_KIB} kB\nMemFree: 1 kB\n"
    commands["nvidia-smi"] = nvidia([12288], ["NVIDIA GeForce RTX 3060"])
    commands["lscpu"] = (0, LSCPU)
    commands["dmidecode"] = (0, DMIDECODE)
    result = probe()
    assert (result["ram_gb"], result["vram_gb"], result["basis"], result["budget_gb"]) == (32.0, 12.0, "gpu", 11.0)
    assert result["gpu_name"] == "NVIDIA GeForce RTX 3060"
    assert (result["cpu_model"], result["cpu_cores"], result["cpu_max_mhz"]) == ("AMD Ryzen 7 5800X 8-Core Processor", 16, 4850)
    assert (result["ram_type"], result["ram_speed_mts"]) == ("DDR4", 3200)
    assert result["recommended"] == fitting(11.0)
    assert "qwen2.5-coder:32b" not in result["recommended"]
    assert "(NVIDIA GeForce RTX 3060)" in result["speed_note"]
    assert all(c["fits"] == (c["size_gb"] <= 11.0) for c in result["all_candidates"])


def test_two_cards_add_their_vram_and_a_repeated_name_is_shown_once(machine):
    commands, _ = machine("Linux")
    commands["nvidia-smi"] = nvidia([24576, 24576], ["NVIDIA RTX 3090", "NVIDIA RTX 3090"])
    result = probe()
    assert (result["vram_gb"], result["gpu_name"], result["budget_gb"]) == (48.0, "NVIDIA RTX 3090", 47.0)
    assert "llama3.1:70b" in result["recommended"]


def test_a_mac_without_nvidia_is_sized_by_its_memory_and_says_it_is_bandwidth_bound(machine):
    commands, _ = machine("Darwin")
    sysctl = {"hw.memsize": str(16 * 1024 ** 3), "machdep.cpu.brand_string": "Apple M2 Pro", "hw.logicalcpu": "12",
              "hw.cpufrequency_max": "3504000000"}
    commands["sysctl"] = lambda cmd: (0, sysctl[cmd[2]] + "\n")
    commands["system_profiler"] = (0, "Memory:\n\n      Type: LPDDR5\n      Speed: 6400 MHz\n")
    result = probe()
    assert (result["ram_gb"], result["vram_gb"], result["basis"], result["budget_gb"]) == (16.0, None, "cpu", 13.0)
    assert (result["cpu_model"], result["cpu_cores"], result["cpu_max_mhz"]) == ("Apple M2 Pro", 12, 3504)
    assert "LPDDR5 at 6400 MT/s" in result["speed_note"]
    assert result["recommended"] == fitting(13.0)


def test_a_windows_pc_reads_its_cpu_and_memory_from_powershell(machine):
    commands, _ = machine("Windows")

    def powershell(cmd):
        script = cmd[-1]
        if "Win32_ComputerSystem" in script:
            return 0, str(64 * 1024 ** 3)
        if "Win32_Processor" in script:
            return 0, json.dumps({"Name": "Intel(R) Core(TM) i9-12900K ", "MaxClockSpeed": 3200, "NumberOfLogicalProcessors": 24})
        return 0, json.dumps({"Speed": 4800, "SMBIOSMemoryType": 34})

    commands["powershell"] = powershell
    result = probe()
    assert (result["ram_gb"], result["basis"]) == (64.0, "cpu")
    assert (result["cpu_model"], result["cpu_cores"], result["cpu_max_mhz"]) == ("Intel(R) Core(TM) i9-12900K", 24, 3200)
    assert (result["ram_type"], result["ram_speed_mts"]) == ("DDR5", 4800)


def test_a_windows_pc_whose_powershell_answers_garbage_still_gets_a_result(machine):
    commands, _ = machine("Windows")
    commands["powershell"] = lambda cmd: (0, "not json at all")
    result = probe()
    assert (result["ram_gb"], result["cpu_model"], result["ram_type"]) == (None, None, None)
    assert result["basis"] == "unknown"


def test_linux_without_lscpu_or_dmidecode_falls_back_to_proc_and_says_how_to_get_ram_speed(machine):
    _, files = machine("Linux")
    files["/proc/meminfo"] = f"MemTotal:       {8 * GIB_IN_KIB} kB\n"
    files["/proc/cpuinfo"] = "".join(f"processor : {n}\nmodel name : Intel Celeron N4020\ncpu MHz : {1100 + n}.0\n\n" for n in range(2))
    result = probe()
    assert (result["cpu_model"], result["cpu_cores"], result["cpu_max_mhz"]) == ("Intel Celeron N4020", 2, 1101)
    assert (result["basis"], result["budget_gb"]) == ("cpu", 5.0)
    assert "install dmidecode" in result["speed_note"]


def test_a_machine_nothing_can_be_learned_about_still_gets_the_smallest_model(machine):
    machine("Plan 9")
    result = probe()
    assert (result["ram_gb"], result["vram_gb"], result["basis"], result["budget_gb"]) == (None, None, "unknown", 0)
    assert result["recommended"] == [hardware_probe.CANDIDATE_MODELS[0][0]]
    assert "not supported on this OS" in result["speed_note"]
    assert not any(c["fits"] for c in result["all_candidates"])


def test_a_command_that_is_not_installed_reads_as_unavailable():
    assert asyncio.run(hardware_probe._run(["starfire-no-such-command-here"])) == (-1, "")


def test_a_command_that_answers_is_read():
    code, out = asyncio.run(hardware_probe._run([sys.executable, "-c", "print('12288')"]))
    assert (code, out.strip()) == (0, "12288")
