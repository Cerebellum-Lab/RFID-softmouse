import serial

ser = serial.Serial('COM12', baudrate=9600, timeout=1)

while True:
    raw = ser.readline().decode(errors="ignore").strip()
    if not raw:
        continue
    # Keep only letters and digits
    filtered = ''.join(ch for ch in raw if ch.isalnum())
    if filtered:
        print("Tag:", filtered)