# -*- coding=utf-8 -*-
""""Vendoring script, python 3.5 needed"""
# Taken from pip
# see https://github.com/pypa/pip/blob/95bcf8c5f6394298035a7332c441868f3b0169f4/tasks/vendoring/__init__.py
from pathlib import Path
from pipenv.utils import TemporaryDirectory, mkdir_p
import tarfile
import zipfile
import os
import re
import shutil
import sys
import invoke
import requests

TASK_NAME = 'update'

LIBRARY_OVERRIDES = {
    'requirements-parser': 'requirements',
    'backports.shutil_get_terminal_size': 'backports/shutil_get_terminal_size',
    'backports.weakref': 'backports/weakref',
    'shutil_backports': 'backports/shutil_get_terminal_size',
    'python-dotenv': 'dotenv',
    'pip-tools': 'piptools'
}

# from time to time, remove the no longer needed ones
HARDCODED_LICENSE_URLS = {
    'pytoml': 'https://github.com/avakar/pytoml/raw/master/LICENSE',
    'delegator.py': 'https://raw.githubusercontent.com/kennethreitz/delegator.py/master/LICENSE',
    'click-didyoumean': 'https://raw.githubusercontent.com/click-contrib/click-didyoumean/master/LICENSE',
    'click-completion': 'https://raw.githubusercontent.com/click-contrib/click-completion/master/LICENSE',
    'blindspin': 'https://raw.githubusercontent.com/kennethreitz/delegator.py/master/LICENSE',
    'shutilwhich': 'https://raw.githubusercontent.com/mbr/shutilwhich/master/LICENSE',
    'parse': 'https://raw.githubusercontent.com/techalchemy/parse/master/LICENSE',
    'semver': 'https://raw.githubusercontent.com/k-bx/python-semver/master/LICENSE.txt',
    'crayons': 'https://raw.githubusercontent.com/kennethreitz/crayons/master/LICENSE',
    'pip-tools': 'https://raw.githubusercontent.com/jazzband/pip-tools/master/LICENSE',
    'pew': 'https://raw.githubusercontent.com/berdario/pew/master/LICENSE'
}

FILE_WHITE_LIST = (
    'Makefile',
    'vendor.txt',
    'patched.txt',
    '__init__.py',
    'README.rst',
    'appdirs.py',
)

LIBRARY_RENAMES = {
    'pip': 'pip9'
}

PATCHED_RENAMES = {
    'pip': 'notpip'
}


def drop_dir(path):
    if path.exists() and path.is_dir():
        shutil.rmtree(str(path))


def remove_all(paths):
    for path in paths:
        if path.is_dir():
            drop_dir(path)
        else:
            path.unlink()


def log(msg):
    print('[vendoring.%s] %s' % (TASK_NAME, msg))


def _get_vendor_dir(ctx):
    git_root = ctx.run('git rev-parse --show-toplevel', hide=True).stdout
    return Path(git_root.strip()) / 'pipenv' / 'vendor'


def _get_patched_dir(ctx):
    git_root = ctx.run('git rev-parse --show-toplevel', hide=True).stdout
    return Path(git_root.strip()) / 'pipenv' / 'patched'


def clean_vendor(ctx, vendor_dir):
    # Old _vendor cleanup
    remove_all(vendor_dir.glob('*.pyc'))
    log('Cleaning %s' % vendor_dir)
    for item in vendor_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(str(item))
        elif "LICENSE" in item.name or "COPYING" in item.name:
            continue
        elif item.name not in FILE_WHITE_LIST:
            item.unlink()
        else:
            log('Skipping %s' % item)


def detect_vendored_libs(vendor_dir):
    retval = []
    for item in vendor_dir.iterdir():
        if item.is_dir():
            retval.append(item.name)
        elif "LICENSE" in item.name or "COPYING" in item.name:
            continue
        elif item.name.endswith(".pyi"):
            continue
        elif item.name not in FILE_WHITE_LIST:
            retval.append(item.name[:-3])
    return retval


def rewrite_imports(package_dir, vendored_libs):
    parent = package_dir.parent
    if package_dir.name in LIBRARY_RENAMES and (parent / LIBRARY_RENAMES[package_dir.name]).exists():
        package_dir = parent / LIBRARY_RENAMES[package_dir.name]
    elif package_dir.name in PATCHED_RENAMES and (parent / PATCHED_RENAMES[package_dir.name]).exists():
        package_dir = parent / PATCHED_RENAMES[package_dir.name]
    for item in package_dir.iterdir():
        if item.is_dir():
            rewrite_imports(item, vendored_libs)
        elif item.name.endswith('.py'):
            rewrite_file_imports(item, vendored_libs)


def rewrite_file_imports(item, vendored_libs):
    """Rewrite 'import xxx' and 'from xxx import' for vendored_libs"""
    text = item.read_text(encoding='utf-8')
    for lib in vendored_libs:
        text = re.sub(
            r'(\n\s*)import %s(\n\s*)' % lib,
            r'\1from .vendor import %s\2' % lib,
            text,
        )
        text = re.sub(
            r'(\n\s*)from %s' % lib,
            r'\1from .vendor.%s' % lib,
            text,
        )
    item.write_text(text, encoding='utf-8')


def apply_patch(ctx, patch_file_path):
    log('Applying patch %s' % patch_file_path.name)
    ctx.run('git apply --verbose %s' % patch_file_path)


@invoke.task
def update_safety(ctx):
    ignore_subdeps = ['pip', 'pip-egg-info', 'bin']
    ignore_files = ['pip-delete-this-directory.txt', 'PKG-INFO']
    vendor_dir = _get_patched_dir(ctx)
    log('Using vendor dir: %s' % vendor_dir)
    log('Downloading safety package files...')
    build_dir = vendor_dir / 'build'
    download_dir = TemporaryDirectory(prefix='pipenv-', suffix='-safety')
    if build_dir.exists() and build_dir.is_dir():
        drop_dir(build_dir)

    ctx.run(
        'pip download -b {0} --no-binary=:all: --no-clean -d {1} safety pyyaml'.format(
            str(build_dir), str(download_dir.name),
        )
    )
    safety_dir = build_dir / 'safety'
    yaml_build_dir = build_dir / 'pyyaml'
    main_file = safety_dir / '__main__.py'
    main_content = """
from safety.cli import cli

# Disable insecure warnings.
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

cli(prog_name="safety")
    """.strip()
    with open(str(main_file), 'w') as fh:
        fh.write(main_content)

    with ctx.cd(str(safety_dir)):
        ctx.run('pip install --no-compile --no-binary=:all: -t . .')
        safety_dir = safety_dir.absolute()
        yaml_dir = safety_dir / 'yaml'
        if yaml_dir.exists():
            version_choices = ['2', '3']
            version_choices.remove(str(sys.version_info[0]))
            mkdir_p(str(yaml_dir / 'yaml{0}'.format(sys.version_info[0])))
            for fn in yaml_dir.glob('*.py'):
                fn.rename(str(fn.parent.joinpath('yaml{0}'.format(sys.version_info[0]), fn.name)))
            if version_choices[0] == '2':
                lib = yaml_build_dir / 'lib' / 'yaml'
            else:
                lib = yaml_build_dir / 'lib3' / 'yaml'
            shutil.copytree(str(lib.absolute()), str(yaml_dir / 'yaml{0}'.format(version_choices[0])))
            yaml_init = yaml_dir / '__init__.py'
            yaml_init.write_text("""
import sys
if sys.version_info[0] == 3:
    from .yaml3 import *
else:
    from .yaml2 import *
            """.strip())
        requests_dir = safety_dir / 'requests'
        cacert = vendor_dir / 'requests' / 'cacert.pem'
        if not cacert.exists():
            from pipenv.vendor import requests
            cacert = Path(requests.certs.where())
        target_cert = requests_dir / 'cacert.pem'
        target_cert.write_bytes(cacert.read_bytes())
        ctx.run("sed -i 's/r = requests.get(url=url, timeout=REQUEST_TIMEOUT, headers=headers)/r = requests.get(url=url, timeout=REQUEST_TIMEOUT, headers=headers, verify=False)/g' {0}".format(str(safety_dir / 'safety' / 'safety.py')))
        for egg in safety_dir.glob('*.egg-info'):
            drop_dir(egg.absolute())
        for dep in ignore_subdeps:
            dep_dir = safety_dir / dep
            if dep_dir.exists():
                drop_dir(dep_dir)
        for dep in ignore_files:
            fn = safety_dir / dep
            if fn.exists():
                fn.unlink()
    zip_name = '{0}/safety'.format(str(vendor_dir))
    shutil.make_archive(zip_name, format='zip', root_dir=str(safety_dir), base_dir='./')
    drop_dir(build_dir)
    download_dir.cleanup()


def get_patched(ctx):
    log('Reinstalling patched libraries')
    patched_dir = _get_patched_dir(ctx)
    ctx.run(
        'pip install -t {0} -r {0}/patched.txt --no-compile --no-deps'.format(
            str(patched_dir),
        )
    )
    remove_all(patched_dir.glob('*.dist-info'))
    remove_all(patched_dir.glob('*.egg-info'))
    drop_dir(patched_dir / 'bin')
    drop_dir(patched_dir / 'tests')

    # Detect the vendored packages/modules
    vendored_libs = detect_vendored_libs(patched_dir)
    log("Detected vendored libraries: %s" % ", ".join(vendored_libs))

    # Special cases: apply stored patches
    log("Apply patches")
    patch_dir = Path(__file__).parent / 'patches'
    current_dir = os.path.abspath(os.curdir)
    os.chdir(str(patched_dir))
    git_root = ctx.run('git rev-parse --show-toplevel', hide=True).stdout.strip()
    os.chdir(git_root)
    try:
        for patch in patch_dir.glob('*.patch'):
            apply_patch(ctx, patch)
    finally:
        os.chdir(current_dir)

    # Global import rewrites
    log("Rewriting all imports related to vendored libs")
    for item in patched_dir.iterdir():
        if item.is_dir():
            if item.name in PATCHED_RENAMES:
                new_path = item.parent / PATCHED_RENAMES[item.name]
                item.rename(str(new_path))
            rewrite_imports(item, vendored_libs)
            if item.name == 'backports':
                backport_init = item / '__init__.py'
                backport_libs = detect_vendored_libs(item)
                init_content = backport_init.read_text().splitlines()
                for lib in backport_libs:
                    init_content.append('from . import {0}'.format(lib))
                backport_init.write_text('\n'.join(init_content) + '\n')
        elif item.name not in FILE_WHITE_LIST:
            rewrite_file_imports(item, vendored_libs)


def vendor(ctx, vendor_dir):
    log('Reinstalling vendored libraries')
    # We use --no-deps because we want to ensure that all of our dependencies
    # are added to vendor.txt, this includes all dependencies recursively up
    # the chain.
    ctx.run(
        'pip install -t {0} -r {0}/vendor.txt --no-compile --no-deps'.format(
            str(vendor_dir),
        )
    )
    remove_all(vendor_dir.glob('*.dist-info'))
    remove_all(vendor_dir.glob('*.egg-info'))

    # Cleanup setuptools unneeded parts
    drop_dir(vendor_dir / 'bin')
    drop_dir(vendor_dir / 'tests')

    # Detect the vendored packages/modules
    vendored_libs = detect_vendored_libs(vendor_dir)
    log("Detected vendored libraries: %s" % ", ".join(vendored_libs))

    # Global import rewrites
    log("Rewriting all imports related to vendored libs")
    for item in vendor_dir.iterdir():
        if item.is_dir():
            if item.name in LIBRARY_RENAMES:
                new_path = item.parent / LIBRARY_RENAMES[item.name]
                item.rename(str(new_path))
            rewrite_imports(item, vendored_libs)
            if item.name == 'backports':
                backport_init = item / '__init__.py'
                backport_libs = detect_vendored_libs(item)
                init_content = backport_init.read_text().splitlines()
                for lib in backport_libs:
                    init_content.append('from . import {0}'.format(lib))
                backport_init.write_text('\n'.join(init_content) + '\n')
        elif item.name not in FILE_WHITE_LIST:
            rewrite_file_imports(item, vendored_libs)


@invoke.task
def rewrite_all_imports(ctx):
    vendor_dir = _get_vendor_dir(ctx)
    log('Using vendor dir: %s' % vendor_dir)
    vendored_libs = detect_vendored_libs(vendor_dir)
    log("Detected vendored libraries: %s" % ", ".join(vendored_libs))
    log("Rewriting all imports related to vendored libs")
    for item in vendor_dir.iterdir():
        if item.is_dir():
            rewrite_imports(item, vendored_libs)
        elif item.name not in FILE_WHITE_LIST:
            rewrite_file_imports(item, vendored_libs)


@invoke.task
def download_licenses(ctx, vendor_dir, requirements_file='vendor.txt'):
    log('Downloading licenses')
    if not vendor_dir:
        vendor_dir = _get_vendor_dir(ctx)
    tmp_dir = vendor_dir / '__tmp__'
    ctx.run(
        'pip download -r {0}/{1} --no-binary :all: --no-deps -d {2}'.format(
            str(vendor_dir),
            requirements_file,
            str(tmp_dir),
        )
    )
    for sdist in tmp_dir.iterdir():
        extract_license(vendor_dir, sdist)
    drop_dir(tmp_dir)


def extract_license(vendor_dir, sdist):
    if sdist.stem.endswith('.tar'):
        ext = sdist.suffix[1:]
        with tarfile.open(sdist, mode='r:{}'.format(ext)) as tar:
            found = find_and_extract_license(vendor_dir, tar, tar.getmembers())
    elif sdist.suffix == '.zip':
        with zipfile.ZipFile(sdist) as zip:
            found = find_and_extract_license(vendor_dir, zip, zip.infolist())
    else:
        raise NotImplementedError('new sdist type!')

    if not found:
        log('License not found in {}, will download'.format(sdist.name))
        license_fallback(vendor_dir, sdist.name)


def find_and_extract_license(vendor_dir, tar, members):
    found = False
    for member in members:
        try:
            name = member.name
        except AttributeError:  # zipfile
            name = member.filename
        if 'LICENSE' in name or 'COPYING' in name:
            if '/test' in name:
                # some testing licenses in hml5lib and distlib
                log('Ignoring {}'.format(name))
                continue
            found = True
            extract_license_member(vendor_dir, tar, member, name)
    return found


def license_fallback(vendor_dir, sdist_name):
    """Hardcoded license URLs. Check when updating if those are still needed"""
    for libname, url in HARDCODED_LICENSE_URLS.items():
        if libname in sdist_name:
            _, _, name = url.rpartition('/')
            dest = license_destination(vendor_dir, libname, name)
            r = requests.get(url, allow_redirects=True)
            log('Downloading {}'.format(url))
            r.raise_for_status()
            dest.write_bytes(r.content)
            return
    raise ValueError('No hardcoded URL for {} license'.format(sdist_name))


def libname_from_dir(dirname):
    """Reconstruct the library name without it's version"""
    parts = []
    for part in dirname.split('-'):
        if part[0].isdigit():
            break
        parts.append(part)
    return'-'.join(parts)


def license_destination(vendor_dir, libname, filename):
    """Given the (reconstructed) library name, find appropriate destination"""
    normal = vendor_dir / libname
    if normal.is_dir():
        return normal / filename
    lowercase = vendor_dir / libname.lower()
    if lowercase.is_dir():
        return lowercase / filename
    rename_dict = LIBRARY_RENAMES if vendor_dir.name != 'patched' else PATCHED_RENAMES
    if libname in rename_dict:
        return vendor_dir / rename_dict[libname] / filename
    if libname in LIBRARY_OVERRIDES:
        override = vendor_dir / LIBRARY_OVERRIDES[libname]
        if not override.exists() and override.parent.exists():
            # for flattened subdeps, specifically backports/weakref.py
            target_dir = vendor_dir / override.parent
            target_file = '{0}.{1}'.format(override.name, filename)
            return target_dir / target_file
        return vendor_dir / LIBRARY_OVERRIDES[libname] / filename
    # fallback to libname.LICENSE (used for nondirs)
    return vendor_dir / '{}.{}'.format(libname, filename)


def extract_license_member(vendor_dir, tar, member, name):
    mpath = Path(name)  # relative path inside the sdist
    dirname = list(mpath.parents)[-2].name  # -1 is .
    libname = libname_from_dir(dirname)
    dest = license_destination(vendor_dir, libname, mpath.name)
    # dest_relative = dest.relative_to(Path.cwd())
    # log('Extracting {} into {}'.format(name, dest_relative))
    log('Extracting {} into {}'.format(name, dest))
    try:
        fileobj = tar.extractfile(member)
        dest.write_bytes(fileobj.read())
    except AttributeError:  # zipfile
        dest.write_bytes(tar.read(member))


@invoke.task
def update_stubs(ctx):
    vendor_dir = _get_vendor_dir(ctx)
    vendored_libs = detect_vendored_libs(vendor_dir)

    print("[vendoring.update_stubs] Add mypy stubs")

    extra_stubs_needed = {
        # Some projects need stubs other than a simple <name>.pyi
        "six": ["six.__init__", "six.moves"],
        # Some projects should not have stubs coz they're single file modules
        "appdirs": [],
    }

    for lib in vendored_libs:
        if lib not in extra_stubs_needed:
            (vendor_dir / (lib + ".pyi")).write_text("from %s import *" % lib)
            continue

        for selector in extra_stubs_needed[lib]:
            fname = selector.replace(".", os.sep) + ".pyi"
            if selector.endswith(".__init__"):
                selector = selector[:-9]

            f_path = vendor_dir / fname
            if not f_path.parent.exists():
                f_path.parent.mkdir()
        f_path.write_text("from %s import *" % selector)


@invoke.task(name=TASK_NAME)
def main(ctx):
    vendor_dir = _get_vendor_dir(ctx)
    patched_dir = _get_patched_dir(ctx)
    log('Using vendor dir: %s' % vendor_dir)
    clean_vendor(ctx, vendor_dir)
    clean_vendor(ctx, patched_dir)
    vendor(ctx, vendor_dir)
    get_patched(ctx)
    download_licenses(ctx, vendor_dir)
    download_licenses(ctx, patched_dir, 'patched.txt')
    # update_safety(ctx)
    log('Revendoring complete')