"""Module for DataIO _FileData

Populate and verify stuff in the 'file' block in fmu (partial excpetion is checksum_md5
as this is convinient to populate later, on demand)
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Optional
from warnings import warn

from fmu.dataio._definitions import FmuContext
from fmu.dataio._logging import null_logger
from fmu.dataio._utils import export_file_compute_checksum_md5
from fmu.dataio.datastructure.meta import meta

logger: Final = null_logger(__name__)

if TYPE_CHECKING:
    from fmu.dataio import ExportData, types

    from .objectdata._provider import ObjectDataProvider


@dataclass
class FileDataProvider:
    """Class for providing metadata for the 'files' block in fmu-dataio.

    Example::

        file:
            relative_path: ... (relative to case)
            absolute_path: ...
    """

    # input
    dataio: ExportData
    objdata: ObjectDataProvider
    rootpath: Path = field(default_factory=Path)
    itername: str = ""
    realname: str = ""
    obj: Optional[types.Inferrable] = field(default=None)
    compute_md5: bool = False

    # storing results in these variables
    forcefolder_is_absolute: bool = field(default=False, init=False)

    @property
    def name(self) -> str:
        return self.dataio.name or self.objdata.name

    def get_metadata(self) -> meta.File:
        relpath, symrelpath = self._get_path()
        relative_path, absolute_path = self._derive_filedata_generic(relpath)
        relative_path_symlink, absolute_path_symlink = (
            self._derive_filedata_generic(symrelpath) if symrelpath else (None, None)
        )
        logger.info("Returning metadata pydantic model meta.File")
        return meta.File(
            absolute_path=absolute_path,
            relative_path=relative_path,
            checksum_md5=self._compute_md5() if self.compute_md5 else None,
            relative_path_symlink=relative_path_symlink,
            absolute_path_symlink=absolute_path_symlink,
        )

    def _derive_filedata_generic(self, inrelpath: Path) -> tuple[Path, Path]:
        """This works with both normal data and symlinks."""
        stem = self._get_filestem()

        path = Path(inrelpath) / stem.lower()
        path = path.with_suffix(path.suffix + self.objdata.extension)

        # resolve() will fix ".." e.g. change '/some/path/../other' to '/some/other'
        abspath = path.resolve()

        try:
            str(abspath).encode("ascii")
        except UnicodeEncodeError:
            print(f"!! Path has non-ascii elements which is not supported: {abspath}")
            raise

        if self.forcefolder_is_absolute:
            # may become meaningsless as forcefolder can be something else, but will try
            try:
                relpath = path.relative_to(self.rootpath)
            except ValueError as verr:
                if ("does not start with" in str(verr)) or (
                    "not in the subpath of" in str(verr)
                ):
                    relpath = abspath
                    logger.info(
                        "Relative path equal to absolute path due to forcefolder "
                        "with absolute path deviating for rootpath %s",
                        self.rootpath,
                    )
                else:
                    raise
        else:
            relpath = path.relative_to(self.rootpath)

        logger.info("Derived filedata")
        return relpath, abspath

    def _compute_md5(self) -> str:
        """Compute an MD5 sum using a temporary file."""
        if self.obj is None:
            raise ValueError("Can't compute MD5 sum without an object.")
        if not self.objdata.extension.startswith("."):
            raise ValueError("An extension must start with '.'")

        with NamedTemporaryFile(buffering=0, suffix=self.objdata.extension) as tf:
            logger.info("Compute MD5 sum for tmp file...: %s", tf.name)
            return export_file_compute_checksum_md5(
                obj=self.obj, filename=Path(tf.name), flag=self.dataio._usefmtflag
            )

    def _get_filestem(self) -> str:
        """Construct the file"""

        if not self.name:
            raise ValueError("The 'name' entry is missing for constructing a file name")
        if not self.objdata.time0 and self.objdata.time1:
            raise ValueError("Not legal: 'time0' is missing while 'time1' is present")

        stem = self.name.lower()
        if self.dataio.tagname:
            stem += "--" + self.dataio.tagname.lower()
        if self.dataio.parent:
            stem = self.dataio.parent.lower() + "--" + stem

        if self.objdata.time0 and not self.objdata.time1:
            stem += "--" + (str(self.objdata.time0)[0:10]).replace("-", "")

        elif self.objdata.time0 and self.objdata.time1:
            monitor = (str(self.objdata.time1)[0:10]).replace("-", "")
            base = (str(self.objdata.time0)[0:10]).replace("-", "")
            if monitor == base:
                warn(
                    "The monitor date and base date are equal", UserWarning
                )  # TODO: consider add clocktimes in such cases?
            if self.dataio.filename_timedata_reverse:  # class variable
                stem += "--" + base + "_" + monitor
            else:
                stem += "--" + monitor + "_" + base

        # remove unwanted characters
        stem = stem.replace(".", "_").replace(" ", "_")

        # avoid multiple double underscores
        while "__" in stem:
            stem = stem.replace("__", "_")

        # treat norwegian special letters
        # BUG(?): What about germen letter like "Ü"?
        stem = stem.replace("æ", "ae")
        stem = stem.replace("ø", "oe")
        return stem.replace("å", "aa")

    def _get_path(self) -> tuple[Path, Path | None]:
        """Construct and get the folder path(s)."""
        linkdest = None

        assert isinstance(
            self.dataio.fmu_context, FmuContext
        )  # Converted to a FmuContext obj. in post-init.

        dest = self._get_path_generic(
            mode=self.dataio.fmu_context,
            allow_forcefolder=True,
        )

        if self.dataio.fmu_context == FmuContext.CASE_SYMLINK_REALIZATION:
            linkdest = self._get_path_generic(
                mode=FmuContext.REALIZATION,
                allow_forcefolder=False,
                info=self.dataio.fmu_context.name,
            )

        return dest, linkdest

    def _get_path_generic(
        self,
        mode: FmuContext,
        allow_forcefolder: bool = True,
        info: str = "",
    ) -> Path:
        """Generically construct and get the folder path and verify."""
        outroot = deepcopy(self.rootpath)

        logger.info("FMU context is %s", mode)
        if mode == FmuContext.REALIZATION:
            if self.realname:
                outroot = outroot / self.realname  # TODO: if missing self.realname?

            if self.itername:
                outroot = outroot / self.itername

        outroot = outroot / "share"

        if mode == FmuContext.PREPROCESSED:
            outroot = outroot / "preprocessed"
            if self.dataio.forcefolder and self.dataio.forcefolder.startswith("/"):
                raise ValueError(
                    "Cannot use absolute path to 'forcefolder' with preprocessed data"
                )

        if mode != FmuContext.PREPROCESSED:
            if self.dataio.is_observation:
                outroot = outroot / "observations"
            else:
                outroot = outroot / "results"

        dest = outroot / self.objdata.efolder  # e.g. "maps"

        if self.dataio.forcefolder and self.dataio.forcefolder.startswith("/"):
            if not self.dataio.allow_forcefolder_absolute:
                raise ValueError(
                    "The forcefolder includes an absolute path, i.e. "
                    "starting with '/'. This is strongly discouraged and is only "
                    "allowed if classvariable allow_forcefolder_absolute is set to True"
                )
            warn("Using absolute paths in forcefolder is not recommended!")

            # absolute if starts with "/", otherwise relative to outroot
            dest = Path(self.dataio.forcefolder).absolute()
            self.forcefolder_is_absolute = True

            if not allow_forcefolder:
                raise RuntimeError(
                    f"You cannot use forcefolder in combination with fmucontext={info}"
                )

        if self.dataio.subfolder:
            dest = dest / self.dataio.subfolder

        if self.dataio.createfolder:
            dest.mkdir(parents=True, exist_ok=True)

        # check that destination actually exists if verifyfolder is True
        if self.dataio.verifyfolder and not dest.exists():
            raise OSError(f"Folder {str(dest)} is not present.")

        return dest