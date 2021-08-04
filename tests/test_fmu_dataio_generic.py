"""Test the main class DataExporter and functions in the dataio module, ExportData."""
import pathlib
from collections import OrderedDict
import logging
import json
import pytest
import xtgeo
import fmu.dataio

# pylint: disable=protected-access

CFG = OrderedDict()
CFG["model"] = {"name": "Test", "revision": "21.0.0"}
CFG["masterdata"] = {
    "smda": {
        "country": [
            {"identifier": "Norway", "uuid": "ad214d85-8a1d-19da-e053-c918a4889309"}
        ],
        "discovery": [{"short_identifier": "abdcef", "uuid": "ghijk"}],
    }
}
CFG["stratigraphy"] = {"TopVolantis": {}}
CFG["access"] = {"someaccess": "jail"}
CFG["model"] = {"revision": "0.99.0"}

RUN = "tests/data/drogon/ertrun1/realization-0/iter-0/rms"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def test_instantate_class_no_keys():
    """Test function _get_meta_master."""
    # it should be possible to parse without any key options
    case = fmu.dataio.ExportData()
    for attr, value in case.__dict__.items():
        print(attr, value)

    assert case._verbosity == "CRITICAL"
    assert case._is_prediction is True


def test_get_meta_dollars():
    """The private routine that provides special <names> (earlier with $ in front)."""
    case = fmu.dataio.ExportData()
    case._config = CFG
    logger.info(case._meta_dollars)
    assert "$schema" in case._meta_dollars
    assert "fmu" in case._meta_dollars["source"]


def test_get_meta_masterdata():
    """The private routine that provides masterdata."""
    case = fmu.dataio.ExportData()
    case._config = CFG
    case._get_meta_masterdata()
    assert case._meta_masterdata["smda"]["country"][0]["identifier"] == "Norway"


def test_get_meta_access():
    """The private routine that provides access."""
    case = fmu.dataio.ExportData()
    case._config = CFG
    case._get_meta_access()
    assert case._meta_access["someaccess"] == "jail"


def test_get_meta_tracklog():
    """The private routine that provides tracklog."""
    # placeholder


def test_process_fmu_model():
    """The (second order) private routine that provides fmu:model"""
    case = fmu.dataio.ExportData()
    case._config = CFG
    fmumodel = case._process_meta_fmu_model()
    assert fmumodel["revision"] == "0.99.0"


def test_process_fmu_realisation():
    """The (second order) private routine that provides realization and iteration."""
    case = fmu.dataio.ExportData()
    case._config = CFG
    case._pwd = pathlib.Path(RUN)

    c_meta, i_meta, r_meta = case._process_meta_fmu_realization_iteration()
    logger.info("========== CASE")
    logger.info("%s", json.dumps(c_meta, indent=2, default=str))
    logger.info("========== ITER")
    logger.info("%s", json.dumps(i_meta, indent=2, default=str))
    logger.info("========== REAL")
    logger.info("%s", json.dumps(r_meta, indent=2, default=str))

    assert r_meta["parameters"]["KVKH_CREVASSE"] == 0.3
    assert r_meta["parameters"]["GLOBVAR"]["VOLON_FLOODPLAIN_VOLFRAC"] == 0.256355
    assert c_meta["uuid"] == "a40b05e8-e47f-47b1-8fee-f52a5116bd37"


def test_raise_userwarning_missing_content(tmp_path):
    """Example on generting a GridProperty without content spesified."""

    gpr = xtgeo.GridProperty(ncol=10, nrow=11, nlay=12)
    gpr.name = "testgp"
    fmu.dataio.ExportData.export_root = tmp_path.resolve()
    fmu.dataio.ExportData.grid_fformat = "roff"

    with pytest.warns(UserWarning, match="is not provided which defaults"):
        exp = fmu.dataio.ExportData()
        exp._pwd = tmp_path
        exp.to_file(gpr)

    assert (tmp_path / "grids" / ".testgp.roff.yml").is_file() is True


def test_exported_filenames(tmp_path):
    """Test that exported filenames are as expected"""

    fmu.dataio.ExportData.export_root = tmp_path.resolve()

    surf = xtgeo.RegularSurface(
        ncol=20, nrow=30, xinc=20, yinc=20, values=0, name="test"
    )

    # test case 1, vanilla
    exp = fmu.dataio.ExportData(
        name="myname",
        content="depth",
    )
    exp._pwd = tmp_path
    exp.to_file(surf)
    assert (tmp_path / "maps" / "myname.gri").is_file() is True
    assert (tmp_path / "maps" / ".myname.gri.yml").is_file() is True

    # test case 2, dots in name
    exp = fmu.dataio.ExportData(
        name="myname.with.dots", content="depth", verbosity="DEBUG"
    )
    exp._pwd = tmp_path

    # for a surface...
    exp.to_file(surf)
    assert (tmp_path / "maps" / "myname_with_dots.gri").is_file() is True
    assert (tmp_path / "maps" / ".myname_with_dots.gri.yml").is_file() is True

    # ...for a grid property...
    gpr = xtgeo.GridProperty(ncol=10, nrow=11, nlay=12)
    gpr.name = "testgp"
    exp.to_file(gpr)
    assert (tmp_path / "grids" / "myname_with_dots.roff").is_file() is True
    assert (tmp_path / "grids" / ".myname_with_dots.roff.yml").is_file() is True

    # ...for a polygon...
    poly = xtgeo.Polygons()
    poly.from_list([(1.0, 2.0, 3.0, 0), (1.0, 2.0, 3.0, 0)])
    exp.to_file(poly)
    assert (tmp_path / "polygons" / "myname_with_dots.csv").is_file() is True
    assert (tmp_path / "polygons" / ".myname_with_dots.csv.yml").is_file() is True

    # ...and for a table.
    table = poly.dataframe
    exp.to_file(table)
    assert (tmp_path / "tables" / "myname_with_dots.csv").is_file() is True
    assert (tmp_path / "tables" / ".myname_with_dots.csv.yml").is_file() is True
