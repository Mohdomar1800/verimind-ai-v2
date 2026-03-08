import datetime
import random
import os

def generate_logs(output_path, count=10000):
    modules = [
        "aes_core", "sha3_main", "otp_ctrl", "otbn_eng", "hmac_ip", 
        "gpio_top", "spi_host", "usbdev", "alert_handler", "rv_dm"
    ]
    
    # We'll create some "high-risk" modules with more errors
    risk_profile = {
        "otbn_eng": 0.35, # 35% failure rate
        "sha3_main": 0.20,
        "rv_dm": 0.15,
        "aes_core": 0.05,
        "otp_ctrl": 0.02,
        "usbdev": 0.12,
    }
    
    with open(output_path, "w") as f:
        start_time = datetime.datetime(2026, 3, 7, 10, 0, 0)
        
        for i in range(count):
            mod = random.choice(modules)
            # Default failure chance 5% if not in risk profile
            failure_chance = risk_profile.get(mod, 0.05)
            
            # Every line is one log entry
            time_offset = i * 1 # 1 second increments
            ts = (start_time + datetime.timedelta(seconds=time_offset)).isoformat() + "Z"
            
            is_fail = random.random() < failure_chance
            status = "fail" if is_fail else "pass"
            severity = random.randint(2, 4) if is_fail else 0
            level = "ERROR" if is_fail else "INFO"
            
            log_line = f"timestamp={ts} | {level} | module={mod} | test={mod}_test_{random.randint(1,50):02d} | status={status} | severity={severity}"
            
            # Add some random metadata to show flexibility
            if random.random() < 0.3:
                log_line += f" | coverage={random.uniform(40, 99):.2f}"
            if is_fail:
                log_line += f" | error_code=0x{random.randint(1000, 9999):x}"
            
            f.write(log_line + "\n")

if __name__ == "__main__":
    target_dir = r"C:\Users\Admin\Downloads\verimind-ai\tests\sample files"
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
    
    output_file = os.path.join(target_dir, "hackathon_demo_10k.log")
    print(f"Generating 10,000 logs at {output_file}...")
    generate_logs(output_file, 10000)
    print("Generation complete!")
