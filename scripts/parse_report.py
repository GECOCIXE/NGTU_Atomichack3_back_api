def parse_report(report_content: str):
    lines = report_content.splitlines()
    errors = {}
    points = []
    
    for line in lines:
        if line.startswith("[#"):
            current_cluster = line
            points = []
        elif line.strip().startswith("Пункты:"):
            points = [p.strip() for p in line.split("Пункты:")[1].split(",")]
        elif line.strip().startswith("- (") and points:
            for point in points:
                if point not in errors:
                    errors[point] = 0
                errors[point] += 1
    
    total_violations = 0
    for line in lines:
        if line.startswith("Всего нарушений (кластеров):"):
            total_violations = int(line.split(":")[1].strip())
    
    error_counts = dict(sorted(errors.items()))
    
    return {
        "error_points": list(error_counts.keys()),
        "error_counts": error_counts,
        "total_violations": total_violations,
        "full_report": report_content
    }