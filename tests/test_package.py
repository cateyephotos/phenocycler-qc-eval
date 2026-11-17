def test_version():
    import phenocycler_qc_eval
    assert phenocycler_qc_eval.__version__ == "0.1.0"


def test_dependencies_importable():
    import numpy, scipy, skimage, pandas, matplotlib, tifffile, zarr, imagecodecs
    assert int(zarr.__version__.split(".")[0]) >= 3
