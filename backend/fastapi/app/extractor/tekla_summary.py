def generate_summary(data):

    total_members = len(data)

    beams = 0
    columns = 0

    for item in data:

        if item["Direction"] == "Vertical":
            columns += 1
        else:
            beams += 1

    return {

        "TotalMembers": total_members,
        "TotalBeams": beams,
        "TotalColumns": columns
    }