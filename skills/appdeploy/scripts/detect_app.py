#!/usr/bin/env python3
"""Detect app type, framework, and recommend deployment platforms."""

import json
import os
import re
import sys
from pathlib import Path


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def detect(project_path):
    p = Path(project_path)
    result = {
        "app_type": "unknown",
        "framework": None,
        "language": None,
        "has_dockerfile": False,
        "package_manager": None,
        "build_command": None,
        "output_dir": None,
        "existing_platform_config": None,
        "recommended_platforms": [],
        "notes": [],
    }

    # Check existing platform configs
    platform_files = {
        "vercel.json": "vercel",
        "fly.toml": "flyio",
        "railway.json": "railway",
        "render.yaml": "render",
    }
    for fname, platform in platform_files.items():
        if (p / fname).exists():
            result["existing_platform_config"] = platform
            result["notes"].append(f"Found existing {fname} — project configured for {platform}")
            break

    has_procfile = (p / "Procfile").exists()
    if has_procfile:
        result["notes"].append("Found Procfile")

    result["has_dockerfile"] = (p / "Dockerfile").exists()
    if result["has_dockerfile"]:
        result["notes"].append("Found Dockerfile")

    # Detect package manager and Node.js project
    pkg = read_json(p / "package.json")
    if pkg is not None:
        # Package manager detection
        if (p / "pnpm-lock.yaml").exists():
            result["package_manager"] = "pnpm"
        elif (p / "yarn.lock").exists():
            result["package_manager"] = "yarn"
        elif (p / "bun.lockb").exists():
            result["package_manager"] = "bun"
        else:
            result["package_manager"] = "npm"

        pm = result["package_manager"]
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        scripts = pkg.get("scripts", {})

        # Detect language
        if "typescript" in deps or (p / "tsconfig.json").exists():
            result["language"] = "typescript"
        else:
            result["language"] = "javascript"

        # Detect build command
        if "build:prod" in scripts:
            result["build_command"] = f"{pm} run build:prod"
        elif "build" in scripts:
            result["build_command"] = f"{pm} run build"

        # Detect framework
        framework_map = [
            ("next", "nextjs"),
            ("nuxt", "nuxt"),
            ("@sveltejs/kit", "sveltekit"),
            ("svelte", "svelte"),
            ("astro", "astro"),
            ("vue", "vue"),
            ("react", "react"),
            ("hono", "hono"),
            ("fastify", "fastify"),
            ("express", "express"),
            ("koa", "koa"),
        ]
        for dep, fw in framework_map:
            if dep in deps:
                result["framework"] = fw
                break

        # Detect app type and output dir
        ssr_frameworks = {"nextjs", "nuxt", "sveltekit", "astro"}
        backend_frameworks = {"express", "fastify", "hono", "koa"}
        spa_frameworks = {"react", "vue", "svelte"}

        fw = result["framework"]
        if fw in ssr_frameworks:
            result["app_type"] = "frontend-ssr"
            if fw == "nextjs":
                result["output_dir"] = ".next"
            elif fw == "nuxt":
                result["output_dir"] = ".output"
            elif fw == "astro":
                result["output_dir"] = "dist"
        elif fw in backend_frameworks:
            result["app_type"] = "backend-api"
        elif fw in spa_frameworks:
            result["app_type"] = "frontend-spa"
            # Check bundler for output dir
            if "vite" in deps:
                result["output_dir"] = "dist"
                result["notes"].append(f"Detected {fw.capitalize()} with Vite bundler")
            elif fw == "react" and "react-scripts" in deps:
                result["output_dir"] = "build"
                result["notes"].append("Detected React with Create React App")
            else:
                result["output_dir"] = "dist"
        else:
            # Has package.json but no recognized framework
            if "start" in scripts or any(k in deps for k in ("express", "fastify", "hono", "koa")):
                result["app_type"] = "backend-api"
            else:
                result["app_type"] = "frontend-spa"
                result["output_dir"] = "dist"

    # Python project
    elif (p / "requirements.txt").exists() or (p / "pyproject.toml").exists() or (p / "Pipfile").exists():
        result["language"] = "python"
        if (p / "Pipfile").exists():
            result["package_manager"] = "pipenv"
        elif (p / "pyproject.toml").exists():
            result["package_manager"] = "poetry"
        else:
            result["package_manager"] = "pip"

        # Read all Python config files to detect framework
        all_text = ""
        for fname in ("requirements.txt", "pyproject.toml", "Pipfile"):
            content = read_text(p / fname)
            if content:
                all_text += content

        python_frameworks = [
            ("django", "django"),
            ("fastapi", "fastapi"),
            ("flask", "flask"),
            ("streamlit", "streamlit"),
        ]
        for dep, fw in python_frameworks:
            if dep in all_text.lower():
                result["framework"] = fw
                break

        if result["framework"] == "streamlit":
            result["app_type"] = "frontend-ssr"
        else:
            result["app_type"] = "backend-api"

    # Go project
    elif (p / "go.mod").exists():
        result["language"] = "go"
        result["package_manager"] = "go"
        result["app_type"] = "backend-api"
        go_mod = read_text(p / "go.mod") or ""
        for dep, fw in [("gin", "gin"), ("echo", "echo"), ("fiber", "fiber")]:
            if dep in go_mod:
                result["framework"] = fw
                break

    # Rust project
    elif (p / "Cargo.toml").exists():
        result["language"] = "rust"
        result["package_manager"] = "cargo"
        result["app_type"] = "backend-api"
        cargo = read_text(p / "Cargo.toml") or ""
        for dep, fw in [("actix-web", "actix-web"), ("axum", "axum"), ("rocket", "rocket")]:
            if dep in cargo:
                result["framework"] = fw
                break

    # Static site (just index.html, no package.json)
    elif (p / "index.html").exists():
        result["app_type"] = "static"
        result["language"] = "html"
        result["output_dir"] = "."
        result["notes"].append("Plain static site — no build step needed")

    # Containerized-only
    if result["app_type"] == "unknown" and result["has_dockerfile"]:
        result["app_type"] = "containerized"

    # Recommend platforms
    platform_recs = {
        "static": ["vercel", "appdeploy-ai", "render"],
        "frontend-spa": ["vercel", "render", "railway"],
        "frontend-ssr": ["vercel", "railway", "flyio"],
        "backend-api": ["railway", "flyio", "render"],
        "fullstack": ["railway", "flyio", "render"],
        "containerized": ["railway", "flyio", "render"],
    }
    result["recommended_platforms"] = platform_recs.get(result["app_type"], ["railway", "flyio", "render"])

    # If existing platform config, put that first
    if result["existing_platform_config"]:
        plat = result["existing_platform_config"]
        recs = result["recommended_platforms"]
        if plat in recs:
            recs.remove(plat)
        result["recommended_platforms"] = [plat] + recs

    # If Dockerfile, prefer container-friendly platforms
    if result["has_dockerfile"] and result["app_type"] not in ("static", "frontend-spa"):
        recs = result["recommended_platforms"]
        for plat in ("flyio", "railway"):
            if plat in recs:
                recs.remove(plat)
                recs.insert(0, plat)
        result["recommended_platforms"] = recs

    return result


def main():
    project_path = sys.argv[1] if len(sys.argv) > 1 else "."

    if not os.path.isdir(project_path):
        json.dump({"error": f"Directory not found: {project_path}"}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.exit(1)

    result = detect(project_path)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
