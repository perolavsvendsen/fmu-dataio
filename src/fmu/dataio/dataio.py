"""Module for DataIO class.

The metadata spec is documented as a JSON schema, stored under schema/.
"""

from __future__ import annotations

import warnings
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, List, Literal, Optional, Union
from warnings import warn

import pandas as pd
from pydantic import ValidationError as PydanticValidationError

from . import types
from ._definitions import (
    FmuContext,
    ValidationError,
)
from ._logging import null_logger
from ._metadata import generate_export_metadata
from ._utils import (
    create_symlink,
    detect_inside_rms,  # dataio_examples,
    export_file_compute_checksum_md5,
    export_metadata_file,
    prettyprint_dict,
    read_metadata_from_file,
)
from .aggregation import AggregatedData
from .case import InitializeCase
from .datastructure._internal.internal import AllowedContent
from .datastructure.configuration import global_configuration
from .datastructure.meta import meta
from .providers._fmu import FmuProvider, get_fmu_context_from_environment

# always show PendingDeprecationWarnings
warnings.simplefilter("always", PendingDeprecationWarning)

# DATAIO_EXAMPLES: Final = dataio_examples()
INSIDE_RMS: Final = detect_inside_rms()


logger: Final = null_logger(__name__)

AggregatedData: Final = AggregatedData  # Backwards compatibility alias
InitializeCase: Final = InitializeCase  # Backwards compatibility alias


# ======================================================================================
# Private functions
# ======================================================================================


def _validate_variable(key: str, value: type, legals: dict[str, str | type]) -> bool:
    """Use data from __annotions__ to validate that overriden var. is of legal type."""
    if key not in legals:
        logger.warning("Unsupported key, raise an error")
        raise ValidationError(f"The input key '{key}' is not supported")

    legal_key = legals[key]
    # Potential issue: Eval will use the modules namespace. If given
    #   "from typing import ClassVar" or similar.
    # is missing from the namespace, eval(...) will fail.
    valid_type = eval(legal_key) if isinstance(legal_key, str) else legal_key

    try:
        validcheck = valid_type.__args__
    except AttributeError:
        validcheck = valid_type

    if "typing." not in str(validcheck):
        if not isinstance(value, validcheck):
            logger.warning("Wrong type of value, raise an error")
            raise ValidationError(
                f"The value of '{key}' is of wrong type: {type(value)}. "
                f"Allowed types are {validcheck}"
            )
    else:
        logger.info("Skip type checking of complex types; '%s: %s'", key, validcheck)

    return True


# the two next content key related function may require refactoring/simplification
def _check_content(proposed: str | dict | None) -> Any:
    """Check content and return a validated version."""
    logger.info("Evaluate content")

    content = proposed
    content_specific = None
    logger.debug("content is %s of type %s", str(content), type(content))
    if content is None:
        usecontent = "unset"  # user warnings on this will in _objectdata_provider

    elif isinstance(content, str):
        logger.debug("content is a string")
        if AllowedContent.requires_additional_input(content):
            raise ValidationError(f"content {content} requires additional input")
        usecontent = content
        content_specific = None  # not relevant when content is a string
        logger.debug("usecontent is %s", usecontent)

    elif isinstance(content, dict):
        logger.debug("content is a dictionary")
        usecontent = (list(content.keys()))[0]
        logger.debug("usecontent is %s", usecontent)
        content_specific = content[usecontent]
        logger.debug("content_specific is %s", content_specific)
        if not isinstance(content_specific, dict):
            raise ValueError(
                "Content is incorrectly formatted. When giving content as a dict, "
                "it must be formatted as:"
                "{'mycontent': {extra_key: extra_value} where mycontent is a string "
                "and in the list of valid contents, and extra keys in associated "
                " dictionary must be valid keys for this content."
            )
    else:
        raise ValidationError("The 'content' must be string or dict")

    if usecontent != "unset" and usecontent not in AllowedContent.model_fields:
        raise ValidationError(
            f"Invalid content: <{usecontent}>! "
            f"Valid content: {', '.join(AllowedContent.model_fields.keys())}"
        )

    logger.debug("outgoing content is set to %s", usecontent)
    if content_specific:
        content_specific = _content_validate(usecontent, content_specific)
    else:
        logger.debug("content has no extra information")

    return usecontent, content_specific


def _content_validate(name: str, fields: dict[str, object] | None) -> dict | None:
    try:
        return AllowedContent.model_validate({name: fields}).model_dump(
            exclude_none=True,
            mode="json",
        )[name]
    except PydanticValidationError as e:
        raise ValidationError(
            f"""The field {name} has one or more errors that makes it
impossible to create valid content. The data will still be exported but no
metadata will be made. You are strongly encouraged to correct your
configuration. Invalid configuration may be disallowed in future versions.

Detailed information:
{str(e)}
"""
        )


# ======================================================================================
# Public function to read/load assosiated metadata given a file (e.g. a map file)
# ======================================================================================


def read_metadata(filename: str | Path) -> dict:
    """Read the metadata as a dictionary given a filename.

    If the filename is e.g. /some/path/mymap.gri, the assosiated metafile
    will be /some/path/.mymap.gri.yml (or json?)

    Args:
        filename: The full path filename to the data-object.

    Returns:
        A dictionary with metadata read from the assiated metadata file.
    """
    return read_metadata_from_file(filename)


# ======================================================================================
# ExportData, public class
# ======================================================================================


@dataclass
class ExportData:
    """Class for exporting data with rich metadata in FMU.

    This class sets up the general metadata content to be applied in export. The idea is
    that one ExportData instance can be re-used for several similar export() jobs. For
    example::

        edata = dataio.ExportData(
            config=CFG, content="depth", unit="m", vertical_domain={"depth": "msl"},
            timedata=None, is_prediction=True, is_observation=False,
            tagname="faultlines", workflow="rms structural model",
        )

        for name in ["TopOne", TopTwo", "TopThree"]:
            poly = xtgeo.polygons_from_roxar(PRJ, hname, POL_FOLDER)

            out = ed.export(poly, name=name)

    Almost all keyword settings like ``name``, ``tagname`` etc can be set in both the
    ExportData instance and directly in the ``generate_metadata`` or ``export()``
    function, to provide flexibility for different use cases. If both are set, the
    ``export()`` setting will win followed by ``generate_metadata() and finally
    ExportData()``.

    A note on 'pwd' and 'rootpath' and 'casepath': The 'pwd' is the process working
    directory, which is folder where the process (script) starts. The 'rootpath' is the
    folder from which relative file names are relative to and is normally auto-detected.
    The user can however force set the 'actual' rootpath by providing the input
    `casepath`. In case of running a RMS project interactive on disk::

        /project/foo/resmod/ff/2022.1.0/rms/model                   << pwd
        /project/foo/resmod/ff/2022.1.0/                            << rootpath

        A file:

        /project/foo/resmod/ff/2022.1.0/share/results/maps/xx.gri   << example absolute
                                        share/results/maps/xx.gri   << example relative

    When running an ERT forward job using a normal ERT job (e.g. a script)::

        /scratch/nn/case/realization-44/iter-2                      << pwd
        /scratch/nn/case                                            << rootpath

        A file:

        /scratch/nn/case/realization-44/iter-2/share/results/maps/xx.gri  << absolute
                         realization-44/iter-2/share/results/maps/xx.gri  << relative

    When running an ERT forward job but here executed from RMS::

        /scratch/nn/case/realization-44/iter-2/rms/model            << pwd
        /scratch/nn/case                                            << rootpath

        A file:

        /scratch/nn/case/realization-44/iter-2/share/results/maps/xx.gri  << absolute
                         realization-44/iter-2/share/results/maps/xx.gri  << relative


    Args:
        access_ssdl: DEPRECATED. Optional. A dictionary that will overwrite or append
            to the default ssdl settings read from the config. Example:
            ``{"access_level": "restricted", "rep_include": False}``

        casepath: To override the automatic and actual ``rootpath``. Absolute path to
            the case root. If not provided, the rootpath will be attempted parsed from
            the file structure or by other means. See also fmu_context, where "case"
            may need an explicit casepath!

        classification: Optional. The security classification of this data object, if
            override defaults. Valid values are: ["restricted", "internal"].

        config: Required in order to produce valid metadata, either as key (here) or
            through an environment variable. A dictionary with static settings.
            In the standard case this is read from FMU global variables
            (via fmuconfig). The dictionary must contain some
            predefined main level keys to work with fmu-dataio. If the key is missing or
            key value is None, then it will look for the environment variable
            FMU_GLOBAL_CONFIG to detect the file. If no success in finding the file, a
            UserWarning is made. If both a valid config is provided and
            FMU_GLOBAL_CONFIG is provided in addition, the latter will be used.
            Note that this key shall be set while initializing the instance, ie. it
            cannot be used in ``generate_metadata()`` or ``export()``.
            Note also: If missing or empty, export() may still be done, but without a
            metadata file (this feature may change in future releases).

        content: Optional, default is "depth". Is a string or a dictionary with one key.
            Example is "depth" or {"fluid_contact": {"xxx": "yyy", "zzz": "uuu"}}.
            Content is checked agains a white-list for validation!

        fmu_context: In normal forward models, the fmu_context is ``realization`` which
            is default and will put data per realization. Other contexts may be ``case``
            which will put data relative to the case root (see also casepath). Another
            important context is "preprocessed" which will output to a dedicated
            "preprocessed" folder instead, and metadata will be partially re-used in
            an ERT model run. If a non-FMU run is detected (e.g. you run from project),
            fmu-dataio will detect that and set actual context to None as fall-back
            (unless preprocessed is specified). If this key is not explicitly given it
            will be inferred to be either "case"/"realization"/"non-fmu" based on the
            presence of ERT environment variables.

        description: A multiline description of the data either as a string or a list
            of strings.

        display_name: Optional, set name for clients to use when visualizing.

        forcefolder: This setting shall only be used as exception, and will make it
            possible to output to a non-standard folder. A ``/`` in front will indicate
            an absolute path*; otherwise it will be relative to casepath or rootpath, as
            dependent on the both fmu_context and the is_observations boolean value. A
            typical use-case is forcefolder="seismic" which will replace the "cubes"
            standard folder for Cube output with "seismics". Use with care and avoid if
            possible! (*) For absolute paths, the class variable
            allow_forcefolder_absolute must set to True.

        grid_model: Currently allowed but planned for deprecation

        include_index: This applies to Pandas (table) data only, and if True then the
            index column will be exported. Deprecated, use class variable
            ``table_include_index`` instead

        is_prediction: True (default) if model prediction data

        is_observation: Default is False. If True, then disk storage will be on the
            "share/observations" folder, otherwise on share/result. An exception arise
            if fmu_context is "preprocessed", then the folder will be set to
            "share/processed" irrespective the value of is_observation.

        name: Optional but recommended. The name of the object. If not set it is tried
            to be inferred from the xtgeo/pandas/... object. The name is then checked
            towards the stratigraphy list, and name is replaced with official
            stratigraphic name if found in static metadata `stratigraphy`. For example,
            if "TopValysar" is the model name and the actual name is "Valysar Top Fm."
            that latter name will be used.

        parent: Optional. This key is required for datatype GridProperty, and
            refers to the name of the grid geometry.

        realization: Optional, default is -999 which means that realization shall be
            detected automatically from the FMU run. Can be used to override in rare
            cases. If so, numbers must be >= 0

        rep_include: Optional. Boolean flag for REP to display this data object.

        runpath: TODO! Optional and deprecated. The relative location of the current run
            root. Optional and will in most cases be auto-detected, assuming that FMU
            folder conventions are followed. For an ERT run e.g.
            /scratch/xx/nn/case/realization-0/iter-0/. while in a revision at project
            disc it will the revision root e.g. /project/xx/resmod/ff/21.1.0/.

        subfolder: It is possible to set one level of subfolders for file output.
            The input should only accept a single folder name, i.e. no paths. If paths
            are present, a deprecation warning will be raised.

        tagname: This is a short tag description which be be a part of file name.

        timedata: If given, a list of lists with dates, .e.g.
            [[20200101, "monitor"], [20180101, "base"]] or just [[2021010]]. The output
            to metadata will from version 0.9 be different (API change)

        vertical_domain: This is dictionary with a key and a reference e.g.
            {"depth": "msl"} which is default if missing.

        workflow: Short tag desciption of workflow (as description)

        undef_is_zero: Flags that nans should be considered as zero in aggregations


    .. note:: Comment on time formats

        If two dates are present (i.e. the element represents a difference, the input
        time format is on the form::

            timedata: [[20200101, "monitor"], [20180101, "base"]]

        Hence the last data (monitor) usually comes first.

        In the new version this will shown in metadata files as where the oldest date is
        shown as t0::

            data:
              t0:
                value: 2018010T00:00:00
                description: base
              t1:
                value: 202020101T00:00:00
                description: monitor

        The output files will be on the form: somename--t1_t0.ext

    """

    # ----------------------------------------------------------------------------------
    # This role for this class is to be:
    # - public (end user) interface
    # - collect the full settings from global config, user keys and class variables
    # - process and validate these settings
    # - establish PWD and rootpath
    #
    # Then other classes will further do the detailed metadata processing, cf _MetaData
    # and subsequent classes called by _MetaData
    # ----------------------------------------------------------------------------------

    # class variables
    allow_forcefolder_absolute: ClassVar[bool] = False
    arrow_fformat: ClassVar[str] = "arrow"
    case_folder: ClassVar[str] = "share/metadata"
    createfolder: ClassVar[bool] = True  # deprecated
    cube_fformat: ClassVar[str] = "segy"
    filename_timedata_reverse: ClassVar[bool] = False  # reverse order output file name
    grid_fformat: ClassVar[str] = "roff"
    include_ertjobs: ClassVar[bool] = False  # if True, include jobs.json from ERT
    legacy_time_format: ClassVar[bool] = False  # deprecated
    meta_format: ClassVar[Literal["yaml", "json"]] = "yaml"
    polygons_fformat: ClassVar[str] = "csv"  # or use "csv|xtgeo"
    points_fformat: ClassVar[str] = "csv"  # or use "csv|xtgeo"
    surface_fformat: ClassVar[str] = "irap_binary"
    table_fformat: ClassVar[str] = "csv"
    dict_fformat: ClassVar[str] = "json"
    table_include_index: ClassVar[bool] = False
    verifyfolder: ClassVar[bool] = True  # deprecated
    _inside_rms: ClassVar[bool] = False  # developer only! if True pretend inside RMS

    # input keys (alphabetic)
    access_ssdl: dict = field(default_factory=dict)  # deprecated
    aggregation: bool = False
    casepath: Optional[Union[str, Path]] = None
    classification: Optional[str] = None
    config: dict = field(default_factory=dict)
    content: Optional[Union[dict, str]] = None
    depth_reference: str = "msl"
    description: Union[str, list] = ""
    display_name: Optional[str] = None
    fmu_context: Optional[str] = None
    forcefolder: str = ""
    grid_model: Optional[str] = None
    is_observation: bool = False
    is_prediction: bool = True
    name: str = ""
    undef_is_zero: bool = False
    parent: str = ""
    realization: int = -999
    rep_include: Optional[bool] = None
    reuse_metadata_rule: Optional[str] = None  # deprecated
    runpath: Optional[Union[str, Path]] = None
    subfolder: str = ""
    tagname: str = ""
    timedata: Optional[List[list]] = None
    unit: str = ""
    verbosity: str = "DEPRECATED"  # remove in version 2
    vertical_domain: dict = field(default_factory=dict)
    workflow: Optional[str | dict[str, str]] = None
    table_index: Optional[list] = None

    # some keys that are modified version of input, prepended with _use
    _usecontent: dict | str = field(default_factory=dict, init=False)
    _usefmtflag: str = field(default="", init=False)

    # storing resulting state variables for instance, non-public:
    _metadata: dict = field(default_factory=dict, init=False)
    _pwd: Path = field(default_factory=Path, init=False)
    _config_is_valid: bool = field(default=True, init=False)
    _fmurun: bool = field(default=False, init=False)
    _reuse_metadata: bool = field(default=False, init=False)

    # << NB! storing ACTUAL casepath:
    _rootpath: Path = field(default_factory=Path, init=False)

    # in some cases input object may change class; store the internal variable here:
    _object: types.Inferrable = field(init=False)

    def __post_init__(self) -> None:
        logger.info("Running __post_init__ ExportData")

        self._show_deprecations_or_notimplemented()

        self._fmurun = get_fmu_context_from_environment() != FmuContext.NON_FMU

        # set defaults for mutable keys
        self.vertical_domain = {"depth": "msl"}

        self._validate_content_key()
        self._validate_and_establish_fmucontext()
        self._validate_workflow_key()

        self._config_is_valid = global_configuration.is_valid(self.config)
        if self._config_is_valid:
            # TODO: This needs refinement: _config_is_valid should be removed
            self.config = global_configuration.roundtrip(self.config)

        self._establish_pwd_rootpath()
        logger.info("Ran __post_init__")

    def _validate_access_ssdl_key(self) -> None:
        # The access_ssdl argument is deprecated, replaced by 'rep_include' and
        # 'classification' arguments. While still supported, we don't want to mix old
        # and new. I.e. when someone starts using any of the new arguments, we expect
        # them to move away from 'access_ssdl' completely - in arguments AND in config.

        # Check if we are getting both old and new arguments, and raise if we do.
        if self.classification is not None and "access_level" in self.access_ssdl:
            raise ValueError(
                "Conflicting arguments: When using 'classification', the (legacy) "
                "'access_ssdl' is not supported."
            )

        if self.rep_include is not None and "rep_include" in self.access_ssdl:
            raise ValueError(
                "Conflicting arguments: When using 'rep_include', the (legacy) "
                "'access_ssdl' is not supported."
            )

    def _show_deprecations_or_notimplemented(self) -> None:
        """Warn on deprecated keys or on stuff not implemented yet."""

        if self.runpath:
            warn(
                "The 'runpath' key has currently no function. It will be evaluated for "
                "removal in fmu-dataio version 2. Use 'casepath' instead!",
                UserWarning,
            )

        if self.grid_model:
            warn(
                "The 'grid_model' key has currently no function. It will be evaluated "
                "for removal in fmu-dataio version 2.",
                UserWarning,
            )

        if self.access_ssdl:
            # if the access_ssdl argument is still provided, warning, then validate it
            warn(
                "The 'access_ssdl' key is deprecated, and replaced by arguments "
                "'classification' and 'rep_include'. Please update your code.",
                PendingDeprecationWarning,
            )

            # (I am) not capable of doing a proper validation using e.g. Pydantic here
            # since we are stuck in so many corner-cases:
            # - access_ssdl argument is deprecated, but allowed
            # - We allow partial access_ssdl arg (e.g. just "access_level")
            # - We do not allow non-valid values for access_level, EXCEPT "asset", which
            #   we give warning for. (Time to stop this, perhaps?)
            # TODO

            if self.access_ssdl.get("access_level") == "asset":
                warn(
                    "The value 'asset' for access.ssdl.access_level is deprecated. "
                    "Use 'restricted'.",
                    FutureWarning,
                )
                self.access_ssdl["access_level"] = "restricted"

        if self.legacy_time_format:
            warn(
                "Using the 'legacy_time_format=True' option to create metadata files "
                "with the old format for time is now deprecated. This option has no "
                "longer an effect and will be removed in the near future.",
                UserWarning,
            )
        if not self.createfolder:
            warn(
                "Using the 'createfolder=False' option is now deprecated. "
                "This option has no longer an effect and can safely be removed",
                UserWarning,
            )
        if not self.verifyfolder:
            warn(
                "Using the 'verifyfolder=False' option to create metadata files "
                "This option has no longer an effect and can safely be removed",
                UserWarning,
            )
        if self.reuse_metadata_rule:
            warn(
                "The 'reuse_metadata_rule' key is deprecated and has no effect. "
                "Please remove it from the argument list.",
                UserWarning,
            )
        if self.verbosity != "DEPRECATED":
            warn(
                "Using the 'verbosity' key is now deprecated and will have no "
                "effect and will be removed in near future. Please remove it from the "
                "argument list. Set logging level from client script in the standard "
                "manner instead.",
                UserWarning,
            )
        if isinstance(self.workflow, dict):
            warn(
                "The 'workflow' argument should be given as a string. "
                "Support for dictionary will be deprecated.",
                FutureWarning,
            )

    def _validate_workflow_key(self) -> None:
        if self.workflow:
            if isinstance(self.workflow, str):
                workflow = meta.Workflow(reference=self.workflow)
            elif isinstance(self.workflow, dict):
                workflow = meta.Workflow.model_validate(self.workflow)
            else:
                raise TypeError("'workflow' should be string.")

            self.workflow = workflow.model_dump(mode="json", exclude_none=True)

    def _validate_content_key(self) -> None:
        """Validate the given 'content' input."""
        self._usecontent, self._content_specific = _check_content(self.content)

    def _validate_and_establish_fmucontext(self) -> None:
        """
        Validate the given 'fmu_context' input. if not explicitly given it
        will be established based on the presence of ERT environment variables.
        """

        env_fmu_context = get_fmu_context_from_environment()
        logger.debug("fmu context from input is %s", self.fmu_context)
        logger.debug("fmu context from environment is %s", env_fmu_context)

        # use fmu_context from environment if not explicitly set
        if self.fmu_context is None:
            logger.info(
                "fmu_context is established from environment variables %s",
                env_fmu_context,
            )
            self.fmu_context = env_fmu_context
        else:
            self.fmu_context = FmuContext(self.fmu_context.lower())
        logger.info("FMU context is %s", self.fmu_context)

        if not self._fmurun and self.fmu_context != FmuContext.PREPROCESSED:
            logger.warning(
                "Requested fmu_context is <%s> but since this is detected as a non "
                "FMU run, the actual context is force set to <%s>",
                self.fmu_context,
                FmuContext.NON_FMU,
            )
            self.fmu_context = FmuContext.NON_FMU

    def _update_fmt_flag(self) -> None:
        # treat special handling of "xtgeo" in format name:
        if self.points_fformat == "csv|xtgeo" or self.polygons_fformat == "csv|xtgeo":
            self._usefmtflag = "xtgeo"
        logger.info("Using flag format: <%s>", self._usefmtflag)

    def _update_check_settings(self, newsettings: dict) -> None:
        """Update instance settings (properties) from other routines."""
        logger.info("Try new settings %s", newsettings)

        # derive legal input from dataclass signature
        annots = getattr(self, "__annotations__", {})
        legals = {key: val for key, val in annots.items() if not key.startswith("_")}
        if "config" in legals:
            del legals["config"]  # config cannot be updated

        if "config" in newsettings:
            raise ValueError("Cannot have 'config' outside instance initialization")

        for setting, value in newsettings.items():
            if _validate_variable(setting, value, legals):
                setattr(self, setting, value)
                logger.info("New setting OK for %s", setting)

        self._show_deprecations_or_notimplemented()
        self._validate_content_key()
        self._validate_workflow_key()
        self._validate_and_establish_fmucontext()

    def _establish_pwd_rootpath(self) -> None:
        """Establish state variables pwd and the (initial) rootpath.

        The self._pwd stores the process working directory, i.e. the folder
        from which the process is ran

        The self._rootpath stores the folder from which is the base root for all
        relative output files. This rootpath may be dependent on if this is a FMU run
        or just an interactive run.

        Hence this 'initial' rootpath can be updated later!
        """
        logger.info(
            "Establish pwd and actual casepath, inside RMS flag is %s (actual: %s))",
            ExportData._inside_rms,
            INSIDE_RMS,
        )
        self._pwd = Path().absolute()

        # fmu_context 1: Running RMS, we are in conventionally in rootpath/rms/model
        # fmu_context 2: ERT FORWARD_JOB, at case = rootpath=RUNPATH/../../. level
        # fmu_context 3: ERT WORKFLOW_JOB, running somewhere/anywhere else

        self._rootpath = self._pwd
        if self.casepath and isinstance(self.casepath, (str, Path)):
            self._rootpath = Path(self.casepath).absolute()
            logger.info("The casepath is hard set as %s", self._rootpath)

        else:
            if ExportData._inside_rms or INSIDE_RMS:
                logger.info(
                    "Run from inside RMS: ExportData._inside_rms=%s, INSIDE_RMS=%s",
                    ExportData._inside_rms,
                    INSIDE_RMS,
                )
                self._rootpath = (self._pwd / "../../.").absolute().resolve()
                ExportData._inside_rms = True

        logger.info("pwd:        %s", str(self._pwd))
        logger.info("rootpath:   %s", str(self._rootpath))

    def _check_process_object(self, obj: types.Inferrable) -> None:
        """When obj is file-like, it must be checked + assume preprocessed.

        In addition, if preprocessed, derive the name, tagname, subfolder if present and
        those are not set already.

        For all cases, tie incoming obj to self._object
        """

        if isinstance(obj, (str, Path)):
            obj = Path(obj)
            if not obj.exists():
                raise ValidationError(f"The file {obj} does not exist.")

            self._reuse_metadata = True

            currentmeta = read_metadata(obj)
            if "_preprocessed" not in currentmeta:
                raise ValidationError(
                    "The special entry for preprocessed data <_preprocessed> is"
                    "missing in the metadata. A possible solution is to rerun the"
                    "preprocessed export."
                )
            preprocessed = currentmeta["_preprocessed"]

            self.name = self.name or preprocessed.get("name", "")
            self.tagname = self.tagname or preprocessed.get("tagname", "")
            self.subfolder = self.subfolder or preprocessed.get("subfolder", "")

        self._object = obj

    def _get_fmu_provider(self) -> FmuProvider:
        assert isinstance(self.fmu_context, FmuContext)
        assert isinstance(self.workflow, dict) or self.workflow is None
        return FmuProvider(
            model=self.config.get("model"),
            fmu_context=self.fmu_context,
            casepath_proposed=self.casepath or "",
            include_ertjobs=self.include_ertjobs,
            forced_realization=self.realization,
            workflow=self.workflow,
        )

    # ==================================================================================
    # Public methods:
    # ==================================================================================

    def generate_metadata(
        self,
        obj: types.Inferrable,
        compute_md5: bool = True,
        **kwargs: object,
    ) -> dict:
        """Generate and return the complete metadata for a provided object.

        An object may be a map, 3D grid, cube, table, etc which is of a known and
        supported type.

        Examples of such known types are XTGeo objects (e.g. a RegularSurface),
        a Pandas Dataframe, a PyArrow table, etc.

        Args:
            obj: XTGeo instance, a Pandas Dataframe instance or other supported object.
            compute_md5: If True, compute a MD5 checksum for the exported file.
            **kwargs: For other arguments, see ExportData() input keys. If they
                exist both places, this function will override!

        Returns:
            A dictionary with all metadata.

        Note:
            If the ``compute_md5`` key is False, the ``file.checksum_md5`` will be
            empty. If true, the MD5 checksum will be generated based on export to
            a temporary file, which may be time-consuming if the file is large.
        """

        logger.info("Generate metadata...")
        logger.info("KW args %s", kwargs)

        self._update_check_settings(kwargs)

        self._validate_access_ssdl_key()

        self._check_process_object(obj)  # obj --> self._object

        self._establish_pwd_rootpath()
        self._validate_content_key()
        self._update_fmt_flag()

        fmudata = self._get_fmu_provider() if self._fmurun else None

        # update rootpath based on fmurun or not
        # TODO: Move to ExportData init when/if users are
        # disallowed to update class settings on the export.
        self._rootpath = Path(
            fmudata.get_casepath() if fmudata else str(self._rootpath.absolute())
        )
        logger.debug("Rootpath is now %s", self._rootpath)

        # TODO: refactor the argument list for generate_export_metadata; we do not need
        # both self._object and self...
        self._metadata = generate_export_metadata(
            self._object, self, fmudata, compute_md5=compute_md5
        )

        logger.info("The metadata are now ready!")

        return deepcopy(self._metadata)

    def export(
        self,
        obj: types.Inferrable,
        return_symlink: bool = False,
        **kwargs: Any,
    ) -> str:
        """Export data objects of 'known' type to FMU storage solution with metadata.

        This function will also collect the data spesific class metadata. For "classic"
        files, the metadata will be stored i a YAML file with same name stem as the
        data, but with a . in front and "yml" and suffix, e.g.::

            top_volantis--depth.gri
            .top_volantis--depth.gri.yml

        Args:
            obj: XTGeo instance, a Pandas Dataframe instance or other supported object.
            return_symlink: If fmu_context is 'case_symlink_realization' then the link
                adress will be returned if this is True; otherwise the physical file
                path will be returned.
            **kwargs: For other arguments, see ExportData() input keys. If they
                exist both places, this function will override!

        Returns:
            String: full path to exported item.
        """
        self.table_index = kwargs.get("table_index", self.table_index)
        self.generate_metadata(obj, compute_md5=False, **kwargs)
        metadata = self._metadata
        logger.info("Object type is: %s", type(self._object))  # from generate_metadata

        outfile = Path(metadata["file"]["absolute_path"])
        # create output folders if they don't exist
        outfile.parent.mkdir(parents=True, exist_ok=True)
        metafile = outfile.parent / ("." + str(outfile.name) + ".yml")

        useflag = (
            self.table_include_index
            if isinstance(self._object, pd.DataFrame)
            else self._usefmtflag
        )

        logger.info("Export to file and compute MD5 sum, using flag: <%s>", useflag)

        # inject md5 checksum in metadata
        metadata["file"]["checksum_md5"] = export_file_compute_checksum_md5(
            self._object,
            outfile,
            flag=useflag,  # type: ignore
            # BUG(?): Looks buggy, if flag is bool export_file will blow up.
        )
        logger.info("Actual file is:   %s", outfile)

        if self._config_is_valid:
            export_metadata_file(metafile, metadata, savefmt=self.meta_format)
            logger.info("Metadata file is: %s", metafile)
        else:
            warnings.warn("Data will be exported, but without metadata.", UserWarning)

        # generate symlink if requested
        outfile_target = None
        if metadata["file"].get("absolute_path_symlink"):
            outfile_target = Path(metadata["file"]["absolute_path_symlink"])
            outfile_target.parent.mkdir(parents=True, exist_ok=True)
            create_symlink(str(outfile), str(outfile_target))
            metafile_target = outfile_target.parent / ("." + str(outfile.name) + ".yml")
            create_symlink(str(metafile), str(metafile_target))

        self._metadata = metadata

        if return_symlink and outfile_target:
            return str(outfile_target)
        return str(outfile)
