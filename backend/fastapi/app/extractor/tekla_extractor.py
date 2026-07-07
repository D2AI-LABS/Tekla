def extract_model_json():

    # simulated Tekla output structure
    return {
        "elements": [
            {
                "Name": "Beam1",
                "Geometry": {"Length": 6000, "Height": 300}
            },
            {
                "Name": "Column1",
                "Geometry": {"Length": 4000, "Height": 4000}
            },
            {
                "Name": "Brace1",
                "Geometry": {"Length": 2500, "Height": 250}
            }
        ]
    }