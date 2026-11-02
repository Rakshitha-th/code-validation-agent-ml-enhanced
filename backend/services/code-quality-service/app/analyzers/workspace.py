import os


def write_source_files(tmp_dir: str, files: dict[str, str]) -> dict[str, str]:
    path_map: dict[str, str] = {}

    for relative_path, content in files.items():
        absolute_path = os.path.join(tmp_dir, *relative_path.replace("\\", "/").split("/"))
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        with open(absolute_path, "w", encoding="utf-8", newline="") as source_file:
            source_file.write(content)
        path_map[os.path.normcase(os.path.normpath(absolute_path))] = relative_path

    return path_map


def original_path(path_map: dict[str, str], absolute_path: str) -> str:
    normalized_path = os.path.normcase(os.path.normpath(absolute_path))
    return path_map.get(normalized_path, absolute_path)