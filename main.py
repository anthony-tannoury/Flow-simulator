from datetime import datetime

format_str = '%d-%m-%Y %H:%M'

dt1 = datetime.strptime('01-01-2025', '%d-%m-%Y')
dt2 = datetime.strptime('01-01-2025 01:00', format_str)
print((dt2-dt1).total_seconds() // 60)