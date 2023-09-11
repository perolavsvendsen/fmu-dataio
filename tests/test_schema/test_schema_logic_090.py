"""Test the schema, version 0.9.0"""

import logging
from copy import deepcopy

import jsonschema

import pytest


# pylint: disable=no-member

logger = logging.getLogger(__name__)


# ======================================================================================
# 0.9.0
# ======================================================================================


def test_schema_090_file_block(schema_090, metadata_examples):
    """Test variations on the file block."""

    # get a specific example
    example = metadata_examples["0.9.0"]["surface_depth.yml"]

    # shall validate as-is
    jsonschema.validate(instance=example, schema=schema_090)

    # shall validate without absolute_path
    _example = deepcopy(example)
    del _example["file"]["absolute_path"]
    jsonschema.validate(instance=_example, schema=schema_090)

    # md5 checksum shall be a string
    _example["file"]["checksum_md5"] = 123.4
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_example, schema=schema_090)

    # shall not validate without checksum_md5
    del _example["file"]["checksum_md5"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_example, schema=schema_090)

    # shall validate when checksum is put back in
    _example["file"]["checksum_md5"] = "somechecksum"
    jsonschema.validate(instance=_example, schema=schema_090)

    # shall not validate without relative_path
    del _example["file"]["relative_path"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_example, schema=schema_090)


def test_schema_090_logic_case(schema_090, metadata_examples):
    """Asserting validation failure when illegal contents in case example"""

    example = metadata_examples["0.9.0"]["case.yml"]

    # assert validation with no changes
    jsonschema.validate(instance=example, schema=schema_090)

    # assert validation error when "fmu" is missing
    _example = deepcopy(example)
    del _example["fmu"]

    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_example, schema=schema_090)

    # assert validation error when "fmu.model" is missing
    _example = deepcopy(example)
    del _example["fmu"]["model"]

    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_example, schema=schema_090)


def test_schema_090_logic_fmu_block_aggr_real(schema_090, metadata_examples):
    """Test that fmu.realization and fmu.aggregation are not allowed at the same time"""

    metadata = deepcopy(metadata_examples["0.9.0"]["surface_depth.yml"])
    # check that assumptions for the test is true
    assert "realization" in metadata["fmu"]
    assert "aggregation" not in metadata["fmu"]

    # assert validation as-is
    jsonschema.validate(instance=metadata, schema=schema_090)

    # add aggregation, shall fail. Get this from an actual example that validates.
    _metadata_aggregation = metadata_examples["0.9.0"]["aggregated_surface_depth.yml"]
    metadata["fmu"]["aggregation"] = _metadata_aggregation["fmu"]["aggregation"]

    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=metadata, schema=schema_090)


def test_schema_090_logic_data_top_base(schema_090, metadata_examples):
    """Test require data.top and data.base.

    * Require both data.top and data.base, or none.
    """

    metadata = metadata_examples["0.9.0"]["surface_seismic_amplitude.yml"]

    # check that assumptions for the test is true
    assert "top" in metadata["data"]
    assert "base" in metadata["data"]

    # assert validation as-is
    jsonschema.validate(instance=metadata, schema=schema_090)

    # remove "top" - shall fail
    _metadata = deepcopy(metadata)
    del _metadata["data"]["top"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_metadata, schema=schema_090)

    # remove "base" - shall fail
    _metadata = deepcopy(metadata)
    del _metadata["data"]["base"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_metadata, schema=schema_090)

    # remove both - shall pass
    del _metadata["data"]["top"]
    assert "top" not in _metadata["data"]  # test assumption
    assert "base" not in _metadata["data"]  # test assumption
    jsonschema.validate(instance=_metadata, schema=schema_090)


def test_schema_090_logic_field_outline(schema_090, metadata_examples):
    """Test content-specific rule.

    When content == field_outline, require the field_outline field
    """

    metadata = metadata_examples["0.9.0"]["polygons_field_outline.yml"]

    # check that assumptions for the test is true
    assert metadata["data"]["content"] == "field_outline"
    assert "field_outline" in metadata["data"]

    # assert validation as-is
    jsonschema.validate(instance=metadata, schema=schema_090)

    # assert failure when content is field_outline and fluid_contact is missing
    _metadata = deepcopy(metadata)
    del _metadata["data"]["field_outline"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_metadata, schema=schema_090)


def test_schema_090_logic_field_region(schema_090, metadata_examples):
    """Test content-specific rule: field_region

    When content == field_outline, require the data.field_region field.
    """

    metadata = metadata_examples["0.9.0"]["polygons_field_region.yml"]

    # check assumptions
    assert metadata["data"]["content"] == "field_region"
    assert "field_region" in metadata["data"]
    assert "id" in metadata["data"]["field_region"]
    jsonschema.validate(instance=metadata, schema=schema_090)

    # assert that data.field_region is required
    _metadata = deepcopy(metadata)
    del _metadata["data"]["field_region"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_metadata, schema=schema_090)

    # validation of data.field_region
    _metadata = deepcopy(metadata)
    del _metadata["data"]["field_region"]["id"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_metadata, schema=schema_090)

    _metadata = deepcopy(metadata)
    _metadata["data"]["field_region"]["id"] = "NotANumber"
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_metadata, schema=schema_090)


def test_schema_090_logic_fluid_contact(schema_090, metadata_examples):
    """Test content-specific rule.

    When content == fluid_contact, require the fluid_contact field
    """

    # parse the schema and polygons
    metadata = metadata_examples["0.9.0"]["surface_fluid_contact.yml"]

    # check that assumptions for the test is true
    assert metadata["data"]["content"] == "fluid_contact"
    assert "fluid_contact" in metadata["data"]

    # assert failure when content is fluid_contact and fluid_contact block missing
    _metadata = deepcopy(metadata)
    del _metadata["data"]["fluid_contact"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_metadata, schema=schema_090)


def test_schema_090_masterdata_smda(schema_090, metadata_examples):
    """Test schema logic for masterdata.smda."""

    example = metadata_examples["0.9.0"]["case.yml"]

    # assert validation with no changes
    jsonschema.validate(instance=example, schema=schema_090)

    # assert validation error when masterdata block is missing
    _example = deepcopy(example)
    del _example["masterdata"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_example, schema=schema_090)

    # assert validation error when masterdata.smda is missing
    # print(example["masterdata"])
    _example = deepcopy(example)
    del _example["masterdata"]["smda"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=_example, schema=schema_090)

    # assert validation error when missing attribute
    for block in [
        "country",
        "discovery",
        "field",
        "coordinate_system",
        "stratigraphic_column",
    ]:
        _example = deepcopy(example)
        del _example["masterdata"]["smda"][block]
        with pytest.raises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=_example, schema=schema_090)

    # assert validation error if not correct type
    for block, type_ in [
        ("country", list),
        ("discovery", list),
        ("coordinate_system", dict),
        ("stratigraphic_column", dict),
    ]:
        _example = deepcopy(example)
        assert isinstance(_example["masterdata"]["smda"][block], type_)

        _example["masterdata"]["smda"][block] = "somestring"

        with pytest.raises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=_example, schema=schema_090)


def test_schema_090_data_time(schema_090, metadata_examples):
    """Test schema logic for data.time."""

    # fetch one example that contains the data.time element
    example = metadata_examples["0.9.0"]["surface_seismic_amplitude.yml"]
    assert "time" in example["data"]

    # assert validation with no changes
    jsonschema.validate(instance=example, schema=schema_090)

    # valid when data.time is missing
    _example = deepcopy(example)
    del _example["data"]["time"]
    jsonschema.validate(instance=_example, schema=schema_090)

    # valid when only t0
    _example = deepcopy(example)
    del _example["data"]["time"]["t1"]
    assert "t0" in _example["data"]["time"]  # test assumption
    jsonschema.validate(instance=_example, schema=schema_090)

    # valid without labels
    _example = deepcopy(example)
    del _example["data"]["time"]["t0"]["label"]
    jsonschema.validate(instance=_example, schema=schema_090)

    # NOT valid when other types
    for testvalue in [
        [{"t0": "2020-10-28T14:28:02", "label": "mylabel"}],
        "2020-10-28T14:28:02",
        123,
        123.4,
    ]:
        _example = deepcopy(example)
        _example["data"]["time"] = testvalue
        with pytest.raises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=_example, schema=schema_090)


def test_schema_logic_classification(schema_090, metadata_examples):
    """Test the classification of individual files."""

    # fetch example
    example = deepcopy(metadata_examples["0.9.0"]["surface_depth.yml"])

    # assert validation with no changes
    jsonschema.validate(instance=example, schema=schema_090)

    # assert "internal" and "restricted" validates
    example["access"]["classification"] = "internal"
    jsonschema.validate(instance=example, schema=schema_090)

    example["access"]["classification"] = "restricted"
    jsonschema.validate(instance=example, schema=schema_090)

    # assert erroneous value does not validate
    example["access"]["classification"] = "open"
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=example, schema=schema_090)


def test_schema_logic_data_spec(schema_090, metadata_examples):
    """Test schema logic for data.spec"""

    # fetch surface example
    example_surface = deepcopy(metadata_examples["0.9.0"]["surface_depth.yml"])

    # assert validation with no changes
    jsonschema.validate(instance=example_surface, schema=schema_090)

    # assert data.spec required when class == surface
    del example_surface["data"]["spec"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=example_surface, schema=schema_090)

    # fetch table example
    example_table = deepcopy(metadata_examples["0.9.0"]["table_inplace.yml"])

    # assert validation with no changes
    jsonschema.validate(instance=example_table, schema=schema_090)

    # assert data.spec required when class == table
    del example_table["data"]["spec"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=example_table, schema=schema_090)

    # fetch dictionary example
    example_dict = deepcopy(metadata_examples["0.9.0"]["dictionary_parameters.yml"])

    # assert data.spec is not present
    with pytest.raises(KeyError):
        example_dict["data"]["spec"]

    # assert data.spec not required when class === dictionary
    jsonschema.validate(instance=example_dict, schema=schema_090)
