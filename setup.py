"""Tag the launcher wheel per platform: it bundles a platform-specific engine binary."""

from setuptools import setup
from setuptools.dist import Distribution

try:
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # older setuptools ships bdist_wheel through the wheel package
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        # Without this the package is "pure" and the wheel tag would be
        # py3-none-any, which cannot carry a per-platform binary.
        return True


class bdist_wheel(_bdist_wheel):
    def get_tag(self):
        _python, _abi, platform = super().get_tag()
        return "py3", "none", platform


setup(distclass=BinaryDistribution, cmdclass={"bdist_wheel": bdist_wheel})
