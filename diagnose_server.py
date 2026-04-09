import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any
from typing import Optional
from urllib.parse import urlparse

import requests

try:
    from config import DOCLING_URL
    from config import MINISTRAL_MODEL
    from config import MINISTRAL_URL
except Exception:
    DOCLING_URL = "http://localhost:5001/v1/convert/file"
    MINISTRAL_URL = "http://localhost:11434/api"
    MINISTRAL_MODEL = "ministral-3:3b"


def print_section(title: str) -> None:
    print(f"\n{'=' * 20} {title} {'=' * 20}")


def run_command(command: list[str], timeout: int = 20) -> dict[str, Any]:
    try:
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        elapsed = time.perf_counter() - started
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
            "elapsed_sec": round(elapsed, 3),
        }
    except FileNotFoundError:
        return {"ok": False, "error": "command not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def format_result(name: str, result: dict[str, Any]) -> None:
    print(f"[{name}] ok={result.get('ok', False)}")
    if "error" in result:
        print(f"  error: {result['error']}")
        return
    if "elapsed_sec" in result:
        print(f"  elapsed: {result['elapsed_sec']} s")
    if result.get("returncode") not in (None, 0):
        print(f"  returncode: {result['returncode']}")
    if result.get("stdout"):
        print("  stdout:")
        for line in result["stdout"].splitlines()[:20]:
            print(f"    {line}")
    if result.get("stderr"):
        print("  stderr:")
        for line in result["stderr"].splitlines()[:20]:
            print(f"    {line}")


def ensure_directory(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def get_total_ram_gb() -> Optional[float]:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("sullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.ullTotalPhys / (1024 ** 3), 2)

        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        return round((page_size * page_count) / (1024 ** 3), 2)
    except Exception:
        return None


def tcp_probe(url: str, timeout: float = 3.0) -> dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return {"ok": False, "error": "invalid url"}

    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = time.perf_counter() - started
            return {"ok": True, "host": host, "port": port, "elapsed_sec": round(elapsed, 3)}
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "ok": False,
            "host": host,
            "port": port,
            "elapsed_sec": round(elapsed, 3),
            "error": str(exc),
        }


def http_request(method: str, url: str, timeout: int = 20, **kwargs: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = requests.request(method=method, url=url, timeout=timeout, **kwargs)
        elapsed = time.perf_counter() - started
        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "elapsed_sec": round(elapsed, 3),
            "headers": dict(response.headers),
            "text": response.text[:2000],
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {"ok": False, "elapsed_sec": round(elapsed, 3), "error": str(exc)}


def parse_ollama_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    eval_count = payload.get("eval_count")
    eval_duration = payload.get("eval_duration")
    prompt_eval_count = payload.get("prompt_eval_count")
    prompt_eval_duration = payload.get("prompt_eval_duration")
    total_duration = payload.get("total_duration")

    if isinstance(total_duration, int):
        metrics["total_duration_sec"] = round(total_duration / 1_000_000_000, 3)
    if isinstance(eval_duration, int):
        metrics["eval_duration_sec"] = round(eval_duration / 1_000_000_000, 3)
    if isinstance(prompt_eval_duration, int):
        metrics["prompt_eval_duration_sec"] = round(prompt_eval_duration / 1_000_000_000, 3)
    if isinstance(eval_count, int):
        metrics["eval_count"] = eval_count
    if isinstance(prompt_eval_count, int):
        metrics["prompt_eval_count"] = prompt_eval_count
    if isinstance(eval_count, int) and isinstance(eval_duration, int) and eval_duration > 0:
        metrics["tokens_per_sec"] = round(eval_count / (eval_duration / 1_000_000_000), 2)
    load_duration = payload.get("load_duration")
    if isinstance(load_duration, int):
        metrics["load_duration_sec"] = round(load_duration / 1_000_000_000, 3)
    if isinstance(total_duration, int) and isinstance(eval_duration, int) and isinstance(prompt_eval_duration, int):
        overhead = total_duration - eval_duration - prompt_eval_duration
        if overhead >= 0:
            metrics["non_eval_overhead_sec"] = round(overhead / 1_000_000_000, 3)
    return metrics


def collect_gpu_snapshot() -> dict[str, Any]:
    result = run_command(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,utilization.memory,memory.used,power.draw",
            "--format=csv,noheader,nounits",
        ],
        timeout=5,
    )
    if not result.get("ok") or not result.get("stdout"):
        return {"ok": False, "error": result.get("error") or result.get("stderr") or "no data"}

    first_line = result["stdout"].splitlines()[0]
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) < 4:
        return {"ok": False, "error": f"unexpected output: {first_line}"}

    def to_float(value: str) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    return {
        "ok": True,
        "gpu_util": to_float(parts[0]),
        "mem_util": to_float(parts[1]),
        "memory_used_mb": to_float(parts[2]),
        "power_draw_w": to_float(parts[3]),
    }


def sample_gpu_during(stop_event: threading.Event, interval_sec: float = 0.5) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    while not stop_event.is_set():
        snapshot = collect_gpu_snapshot()
        snapshot["ts"] = round(time.perf_counter(), 3)
        samples.append(snapshot)
        stop_event.wait(interval_sec)
    return samples


def summarize_gpu_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [sample for sample in samples if sample.get("ok")]
    if not valid:
        return {"samples": len(samples), "valid_samples": 0}

    gpu_utils = [sample["gpu_util"] for sample in valid if isinstance(sample.get("gpu_util"), (int, float))]
    mem_utils = [sample["mem_util"] for sample in valid if isinstance(sample.get("mem_util"), (int, float))]
    mem_used = [sample["memory_used_mb"] for sample in valid if isinstance(sample.get("memory_used_mb"), (int, float))]
    power_draw = [sample["power_draw_w"] for sample in valid if isinstance(sample.get("power_draw_w"), (int, float))]

    summary: dict[str, Any] = {
        "samples": len(samples),
        "valid_samples": len(valid),
    }
    if gpu_utils:
        summary["avg_gpu_util"] = round(sum(gpu_utils) / len(gpu_utils), 2)
        summary["max_gpu_util"] = round(max(gpu_utils), 2)
    if mem_utils:
        summary["avg_mem_util"] = round(sum(mem_utils) / len(mem_utils), 2)
        summary["max_mem_util"] = round(max(mem_utils), 2)
    if mem_used:
        summary["avg_memory_used_mb"] = round(sum(mem_used) / len(mem_used), 2)
        summary["max_memory_used_mb"] = round(max(mem_used), 2)
    if power_draw:
        summary["avg_power_draw_w"] = round(sum(power_draw) / len(power_draw), 2)
        summary["max_power_draw_w"] = round(max(power_draw), 2)
    return summary


def run_ollama_request(chat_url: str, payload: dict[str, Any], timeout: int, sample_gpu: bool) -> dict[str, Any]:
    stop_event = threading.Event()
    samples: list[dict[str, Any]] = []
    sampler_thread = None

    if sample_gpu and shutil.which("nvidia-smi"):
        def sampler() -> None:
            nonlocal samples
            samples = sample_gpu_during(stop_event)

        sampler_thread = threading.Thread(target=sampler, daemon=True)
        sampler_thread.start()

    started = time.perf_counter()
    try:
        response = requests.post(chat_url, json=payload, timeout=(10, timeout))
        wall_time = time.perf_counter() - started
        record: dict[str, Any] = {
            "wall_sec": round(wall_time, 3),
            "status_code": response.status_code,
            "ok": response.ok,
        }
        if response.ok:
            data = response.json()
            record["content"] = data.get("message", {}).get("content", "")
            record.update(parse_ollama_metrics(data))
        else:
            record["error"] = response.text[:1000]
        return record
    except Exception as exc:
        wall_time = time.perf_counter() - started
        return {
            "wall_sec": round(wall_time, 3),
            "ok": False,
            "error": str(exc),
        }
    finally:
        stop_event.set()
        if sampler_thread is not None:
            sampler_thread.join(timeout=2)
        if samples:
            record_samples = summarize_gpu_samples(samples)
        else:
            record_samples = {"samples": 0, "valid_samples": 0}
        # Attach GPU summary to the most recently returned dict by mutating locals via explicit variable is messy,
        # so keep it in a function-local attribute returned by wrapper callers.
        run_ollama_request.last_gpu_summary = record_samples


run_ollama_request.last_gpu_summary = {"samples": 0, "valid_samples": 0}


def build_benchmark_profiles() -> list[dict[str, Any]]:
    medium_text = " ".join(["Тест производительности Ministral."] * 180)
    large_text = " ".join(["Контекст для проверки prompt processing и скорости генерации."] * 900)
    return [
        {
            "name": "short_generation",
            "prompt": "Reply with exactly one word: OK",
            "num_ctx": 2048,
            "num_predict": 8,
        },
        {
            "name": "medium_generation",
            "prompt": f"Коротко перескажи смысл текста в 3 пунктах:\n\n{medium_text}",
            "num_ctx": 8192,
            "num_predict": 128,
        },
        {
            "name": "large_context_probe",
            "prompt": f"Прочитай текст и ответь одним словом READY:\n\n{large_text}",
            "num_ctx": 32768,
            "num_predict": 8,
        },
    ]


def benchmark_ollama(base_url: str, model: str, repeats: int, timeout: int, sample_gpu: bool) -> dict[str, Any]:
    chat_url = f"{base_url.rstrip('/')}/chat"
    profiles = build_benchmark_profiles()
    profile_summaries: list[dict[str, Any]] = []

    for profile in profiles:
        runs: list[dict[str, Any]] = []
        for index in range(repeats):
            payload = {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": profile["prompt"]}],
                "options": {
                    "temperature": 0,
                    "num_predict": profile["num_predict"],
                    "num_ctx": profile["num_ctx"],
                },
            }
            record = run_ollama_request(chat_url=chat_url, payload=payload, timeout=timeout, sample_gpu=sample_gpu)
            record["run"] = index + 1
            record["profile"] = profile["name"]
            record["num_ctx"] = profile["num_ctx"]
            record["num_predict"] = profile["num_predict"]
            record["gpu_summary"] = run_ollama_request.last_gpu_summary
            runs.append(record)

        good_runs = [run for run in runs if run.get("ok")]
        tokens_per_sec = [run["tokens_per_sec"] for run in good_runs if "tokens_per_sec" in run]
        wall_times = [run["wall_sec"] for run in good_runs if "wall_sec" in run]
        load_times = [run["load_duration_sec"] for run in good_runs if "load_duration_sec" in run]
        prompt_times = [run["prompt_eval_duration_sec"] for run in good_runs if "prompt_eval_duration_sec" in run]
        overhead_times = [run["non_eval_overhead_sec"] for run in good_runs if "non_eval_overhead_sec" in run]

        profile_summary: dict[str, Any] = {
            "profile": profile["name"],
            "num_ctx": profile["num_ctx"],
            "num_predict": profile["num_predict"],
            "runs": runs,
            "successful_runs": len(good_runs),
            "failed_runs": len(runs) - len(good_runs),
        }
        if wall_times:
            profile_summary["avg_wall_sec"] = round(sum(wall_times) / len(wall_times), 3)
            profile_summary["min_wall_sec"] = round(min(wall_times), 3)
            profile_summary["max_wall_sec"] = round(max(wall_times), 3)
        if tokens_per_sec:
            profile_summary["avg_tokens_per_sec"] = round(sum(tokens_per_sec) / len(tokens_per_sec), 2)
            profile_summary["min_tokens_per_sec"] = round(min(tokens_per_sec), 2)
            profile_summary["max_tokens_per_sec"] = round(max(tokens_per_sec), 2)
        if load_times:
            profile_summary["avg_load_duration_sec"] = round(sum(load_times) / len(load_times), 3)
        if prompt_times:
            profile_summary["avg_prompt_eval_duration_sec"] = round(sum(prompt_times) / len(prompt_times), 3)
        if overhead_times:
            profile_summary["avg_non_eval_overhead_sec"] = round(sum(overhead_times) / len(overhead_times), 3)
        profile_summaries.append(profile_summary)

    overall_tokens = [item["avg_tokens_per_sec"] for item in profile_summaries if "avg_tokens_per_sec" in item]
    overall_wall = [item["avg_wall_sec"] for item in profile_summaries if "avg_wall_sec" in item]

    summary = {
        "profiles": profile_summaries,
        "successful_profiles": len([item for item in profile_summaries if item.get("successful_runs")]),
        "failed_profiles": len([item for item in profile_summaries if not item.get("successful_runs")]),
    }
    if overall_tokens:
        summary["avg_tokens_per_sec"] = round(sum(overall_tokens) / len(overall_tokens), 2)
    if overall_wall:
        summary["avg_wall_sec"] = round(sum(overall_wall) / len(overall_wall), 3)
    return summary


def benchmark_docling(docling_convert_url: str, file_path: str, timeout: int) -> dict[str, Any]:
    if not os.path.exists(file_path):
        return {"ok": False, "error": f"file not found: {file_path}"}

    suffix = os.path.splitext(file_path)[1].lower()
    from_format = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".pptx": "pptx",
        ".txt": "md",
    }.get(suffix, "auto")

    with open(file_path, "rb") as handle:
        files = {"files": (os.path.basename(file_path), handle, "application/octet-stream")}
        data = {
            "from_formats": from_format,
            "to_formats": "html",
            "target_type": "inbody",
            "include_images": "false",
            "image_export_mode": "placeholder",
        }
        started = time.perf_counter()
        try:
            response = requests.post(docling_convert_url, files=files, data=data, timeout=(10, timeout))
            elapsed = time.perf_counter() - started
            result: dict[str, Any] = {
                "ok": response.ok,
                "status_code": response.status_code,
                "elapsed_sec": round(elapsed, 3),
            }
            if response.ok:
                payload = response.json()
                html_content = payload.get("document", {}).get("html_content", "")
                result["html_length"] = len(html_content)
            else:
                result["error"] = response.text[:1000]
            return result
        except Exception as exc:
            elapsed = time.perf_counter() - started
            return {"ok": False, "elapsed_sec": round(elapsed, 3), "error": str(exc)}


def diagnose_gpu() -> dict[str, Any]:
    print_section("GPU")
    has_nvidia_smi = shutil.which("nvidia-smi") is not None
    print(f"nvidia-smi in PATH: {has_nvidia_smi}")
    query = run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total,utilization.gpu,utilization.memory,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ],
        timeout=10,
    )
    format_result("nvidia-smi query", query)
    note = None
    if query.get("ok") and query.get("stdout"):
        gpu_name = query["stdout"].split(",", 1)[0].strip().lower()
        if "tesla m40" in gpu_name:
            note = "Tesla M40 is Maxwell-era and has no Tensor Cores; modern LLM inference is often much slower than RTX 40xx."
            print(f"  note: {note}")

    topo = run_command(["nvidia-smi", "topo", "-m"], timeout=10)
    format_result("nvidia-smi topo", topo)
    return {
        "nvidia_smi_in_path": has_nvidia_smi,
        "query": query,
        "topology": topo,
        "note": note,
    }


def diagnose_docker() -> dict[str, Any]:
    print_section("Docker")
    has_docker = shutil.which("docker") is not None
    print(f"docker in PATH: {has_docker}")
    version = run_command(["docker", "version", "--format", "{{.Server.Version}}"], timeout=20)
    format_result("docker version", version)
    ps = run_command(["docker", "ps", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}"], timeout=20)
    format_result("docker ps", ps)
    return {
        "docker_in_path": has_docker,
        "version": version,
        "ps": ps,
    }


def diagnose_ollama(base_url: str, model: str, repeats: int, timeout: int, sample_gpu: bool) -> dict[str, Any]:
    print_section("Ollama / Ministral")
    print(f"base_url: {base_url}")
    print(f"model: {model}")

    tcp = tcp_probe(base_url)
    print(json.dumps({"tcp_probe": tcp}, ensure_ascii=True, indent=2))

    tags = http_request("GET", f"{base_url.rstrip('/')}/tags", timeout=10)
    print(json.dumps({"tags": {k: v for k, v in tags.items() if k != 'text'}}, ensure_ascii=True, indent=2))
    if tags.get("text"):
        print(f"tags body preview: {tags['text'][:400]}")

    ps = http_request("GET", f"{base_url.rstrip('/')}/ps", timeout=10)
    print(json.dumps({"ps": {k: v for k, v in ps.items() if k != 'text'}}, ensure_ascii=True, indent=2))
    if ps.get("text"):
        print(f"ps body preview: {ps['text'][:400]}")

    summary = benchmark_ollama(base_url=base_url, model=model, repeats=repeats, timeout=timeout, sample_gpu=sample_gpu)
    print(json.dumps({"benchmark": summary}, ensure_ascii=True, indent=2))
    return {
        "base_url": base_url,
        "model": model,
        "tcp_probe": tcp,
        "tags": tags,
        "ps": ps,
        "benchmark": summary,
    }


def diagnose_docling(docling_convert_url: str, sample_file: Optional[str], timeout: int) -> Optional[dict[str, Any]]:
    print_section("Docling")
    print(f"convert_url: {docling_convert_url}")

    tcp = tcp_probe(docling_convert_url)
    print(json.dumps({"tcp_probe": tcp}, ensure_ascii=True, indent=2))

    parsed = urlparse(docling_convert_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    probes: dict[str, Any] = {}
    for probe_path in ("/health", "/v1/health", "/openapi.json", "/docs"):
        result = http_request("GET", f"{base}{probe_path}", timeout=10)
        probes[probe_path] = result
        print(json.dumps({probe_path: {k: v for k, v in result.items() if k != 'text'}}, ensure_ascii=True, indent=2))

    if not sample_file:
        print("No sample file provided; skipping Docling conversion benchmark.")
        return {
            "convert_url": docling_convert_url,
            "tcp_probe": tcp,
            "probes": probes,
            "conversion_benchmark": None,
        }

    result = benchmark_docling(docling_convert_url=docling_convert_url, file_path=sample_file, timeout=timeout)
    print(json.dumps({"conversion_benchmark": result}, ensure_ascii=True, indent=2))
    return {
        "convert_url": docling_convert_url,
        "tcp_probe": tcp,
        "probes": probes,
        "conversion_benchmark": result,
    }


def collect_system_info() -> dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.replace(os.linesep, " "),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "total_ram_gb": get_total_ram_gb(),
    }


def print_system_info(system_info: dict[str, Any]) -> None:
    print_section("System")
    print(f"timestamp: {system_info['timestamp']}")
    print(f"hostname: {system_info['hostname']}")
    print(f"platform: {system_info['platform']}")
    print(f"python: {system_info['python']}")
    print(f"processor: {system_info['processor']}")
    print(f"cpu_count: {system_info['cpu_count']}")
    print(f"total_ram_gb: {system_info['total_ram_gb'] if system_info['total_ram_gb'] is not None else 'unknown'}")


def build_summary_lines(ollama_data: dict[str, Any], docling_data: Optional[dict[str, Any]]) -> list[str]:
    ollama_summary = ollama_data.get("benchmark", {})
    lines: list[str] = []
    avg_tps = ollama_summary.get("avg_tokens_per_sec")
    avg_wall = ollama_summary.get("avg_wall_sec")
    if avg_tps is not None:
        lines.append(f"Ollama average generation speed: {avg_tps} tok/s")
    if avg_wall is not None:
        lines.append(f"Ollama average wall time: {avg_wall} s")
    if docling_data is not None:
        docling_benchmark = docling_data.get("conversion_benchmark")
        if isinstance(docling_benchmark, dict) and docling_benchmark.get("ok"):
            lines.append(f"Docling conversion time: {docling_benchmark.get('elapsed_sec')} s")
        elif isinstance(docling_benchmark, dict) and docling_benchmark.get("error"):
            lines.append(f"Docling conversion failed: {docling_benchmark.get('error')}")
    for profile in ollama_summary.get("profiles", []):
        lines.append(
            f"Profile {profile['profile']}: "
            f"ctx={profile['num_ctx']}, predict={profile['num_predict']}, "
            f"avg_wall={profile.get('avg_wall_sec', 'n/a')} s, "
            f"avg_tps={profile.get('avg_tokens_per_sec', 'n/a')} tok/s, "
            f"load={profile.get('avg_load_duration_sec', 'n/a')} s, "
            f"prompt_eval={profile.get('avg_prompt_eval_duration_sec', 'n/a')} s, "
            f"overhead={profile.get('avg_non_eval_overhead_sec', 'n/a')} s"
        )
    lines.append("If this server uses Tesla M40, low tok/s compared with RTX 4060 is expected.")
    lines.append("Focus on whether GPU is actually used, whether prompt processing is slow at large ctx, and whether the server falls back to CPU.")
    return lines


def print_summary(ollama_data: dict[str, Any], docling_data: Optional[dict[str, Any]]) -> None:
    print_section("Quick Summary")
    for line in build_summary_lines(ollama_data, docling_data):
        print(line)


def save_report(report_dir: str, report_prefix: str, report_data: dict[str, Any]) -> dict[str, str]:
    ensure_directory(report_dir)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_prefix = report_prefix.strip() or "diagnostics"
    json_path = os.path.join(report_dir, f"{safe_prefix}_{timestamp}.json")
    txt_path = os.path.join(report_dir, f"{safe_prefix}_{timestamp}.txt")

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report_data, handle, ensure_ascii=False, indent=2)

    summary_lines = build_summary_lines(report_data["ollama"], report_data.get("docling"))
    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write("Server Diagnostics Report\n")
        handle.write(f"Generated at: {report_data['system']['timestamp']}\n")
        handle.write(f"Hostname: {report_data['system']['hostname']}\n\n")
        handle.write("Quick Summary\n")
        handle.write("-" * 60 + "\n")
        for line in summary_lines:
            handle.write(f"{line}\n")
        handle.write("\nFull report saved in the JSON companion file.\n")

    return {"json": json_path, "text": txt_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Full diagnostics for GPU, Ollama, and Docling server performance.")
    parser.add_argument("--ollama-url", default=MINISTRAL_URL, help="Ollama API base URL, e.g. http://localhost:11434/api")
    parser.add_argument("--model", default=MINISTRAL_MODEL, help="Model name for Ollama benchmark")
    parser.add_argument("--docling-url", default=DOCLING_URL, help="Docling convert URL, e.g. http://localhost:5001/v1/convert/file")
    parser.add_argument("--docling-file", default=None, help="Optional sample file for Docling conversion benchmark")
    parser.add_argument("--repeats", type=int, default=3, help="Number of Ollama benchmark runs")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout in seconds for slow requests")
    parser.add_argument("--no-gpu-sampling", action="store_true", help="Disable GPU sampling during Ollama benchmark")
    parser.add_argument("--report-dir", default="diagnostic_reports", help="Directory where JSON/TXT reports will be saved")
    parser.add_argument("--report-prefix", default="server_diagnostics", help="Filename prefix for generated reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    system_info = collect_system_info()
    print_system_info(system_info)
    gpu_data = diagnose_gpu()
    docker_data = diagnose_docker()
    ollama_data = diagnose_ollama(
        base_url=args.ollama_url,
        model=args.model,
        repeats=max(1, args.repeats),
        timeout=max(10, args.timeout),
        sample_gpu=not args.no_gpu_sampling,
    )
    docling_data = diagnose_docling(
        docling_convert_url=args.docling_url,
        sample_file=args.docling_file,
        timeout=max(10, args.timeout),
    )
    print_summary(ollama_data, docling_data)

    report_data = {
        "system": system_info,
        "gpu": gpu_data,
        "docker": docker_data,
        "ollama": ollama_data,
        "docling": docling_data,
        "args": {
            "ollama_url": args.ollama_url,
            "model": args.model,
            "docling_url": args.docling_url,
            "docling_file": args.docling_file,
            "repeats": args.repeats,
            "timeout": args.timeout,
            "gpu_sampling": not args.no_gpu_sampling,
        },
    }
    report_paths = save_report(args.report_dir, args.report_prefix, report_data)
    print_section("Saved Report")
    print(f"JSON report: {report_paths['json']}")
    print(f"Text report: {report_paths['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())