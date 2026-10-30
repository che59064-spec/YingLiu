"""Build an allowlisted clean source archive and, optionally, a Windows app archive."""
from hashlib import sha256
from pathlib import Path
import argparse
import json
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.7.5'
CORE = (
    'server.py', 'run_app.py', 'engine.py', 'workflows.py', 'prompt_optimizer.py',
    'ai_provider.py', 'prompt_media.py', 'prompt_rules.json', 'video_edit.py',
    'segment_reference.py', 'reference_acceleration.py', 'continuity.py', 'Launcher.cs', 'CanvasWindow.cs',
    'CanvasBrand.cs', 'StartupWindow.cs', 'AssemblyInfo.cs', 'app.manifest', 'app.ico',
    'build.ps1', 'setup.ps1', 'dependencies.json', 'config.example.json', 'requirements.txt',
    'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', '.gitignore', '启动映流.cmd',
    'qa/BuildBrandAssets.cs',
)
TEXT_SUFFIXES = {'.py', '.cs', '.js', '.cjs', '.css', '.html', '.md', '.json', '.ps1', '.txt', '.cmd'}
PRIVATE = re.compile(r'[A-Za-z]:[/\\]Users[/\\][^/\\\s"\']+|wxid_[a-z0-9]|[.]workbuddy|codex[-]runtimes|[A-Za-z]:[/\\]Comfy', re.I)
SECRET = re.compile(r'\bsk-[A-Za-z0-9_-]{25,}\b')


def source_files(root=ROOT):
    files = [root / name for name in CORE if (root / name).is_file()]
    for directory, suffixes in (('web', {'.js', '.cjs', '.css', '.html'}),
                                 ('web/brand', {'.svg', '.png', '.ico'}),
                                 ('scripts', {'.py', '.ps1'}),
                                 ('tests', {'.py'}),
                                 ('docs', {'.md'})):
        files.extend(path for path in (root / directory).glob('*') if path.is_file() and path.suffix in suffixes)
    files.extend(path for path in (root / 'bridge').rglob('*') if path.is_file()
                 and path.suffix in {'.py', '.js', '.cjs'} and '__pycache__' not in path.parts)
    files.extend(path for path in (root / 'docs' / 'assets').glob('*') if path.is_file()
                 and path.suffix in {'.png', '.svg'})
    return sorted(set(files))


def inspect(files, root=ROOT):
    problems = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if root.resolve() not in path.resolve().parents:
            raise ValueError('发布文件不能链接到软件目录外：' + relative)
        if path.suffix in TEXT_SUFFIXES:
            content = path.read_text(encoding='utf-8-sig')
            if PRIVATE.search(content) or SECRET.search(content):
                problems.append(relative)
    if problems:
        raise ValueError('发布文件中存在个人路径或疑似密钥，请先移除：' + ', '.join(problems))


def package(output, portable=False, root=ROOT):
    output = Path(output).resolve()
    files = source_files(root)
    inspect(files, root)
    required = []
    if portable:
        required = ['映流.exe', *('data/desktop-shell-bin/' + name for name in
                    ('Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll',
                     'WebView2Loader.dll', 'WebView2-LICENSE.txt'))]
        for relative in required:
            if not (root / relative).is_file():
                raise ValueError('便携包缺少构建产物，请先运行 build.ps1：' + relative)
    name = 'YingLiu-' + VERSION + ('-windows-x64' if portable else '-source')
    stage = output / name
    if stage.exists():
        raise ValueError('发布目录已存在，请换一个输出位置：' + str(stage))
    stage.mkdir(parents=True)
    for path in files:
        target = stage / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    if portable:
        for relative in required:
            path = root / relative
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    manifest = {path.relative_to(stage).as_posix(): sha256(path.read_bytes()).hexdigest()
                for path in sorted(stage.rglob('*')) if path.is_file()}
    (stage / 'SHA256SUMS.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = output / (name + '.zip')
    pending = archive.with_suffix('.zip.tmp')
    with zipfile.ZipFile(pending, 'w', zipfile.ZIP_DEFLATED) as target:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                target.write(path, path.relative_to(output).as_posix())
    pending.replace(archive)
    return {'folder': str(stage), 'zip': str(archive), 'files': len(manifest),
            'sha256': sha256(archive.read_bytes()).hexdigest(), 'containsUserData': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'release'))
    parser.add_argument('--portable', action='store_true')
    args = parser.parse_args()
    print(json.dumps(package(args.output, args.portable), ensure_ascii=False, indent=2))
