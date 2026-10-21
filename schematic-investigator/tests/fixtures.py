"""Generate deterministic vector PDF fixtures; no external schematic content."""
from pathlib import Path
import pymupdf as fitz


def make_pdf(path):
    doc = fitz.open()
    p = doc.new_page(width=600, height=500)
    p.insert_text((40, 40), "POWER SUPPLY", fontsize=20)
    p.insert_text((100, 150), "U1")
    p.insert_text((130, 170), "TPS54331")
    p.insert_text((100, 210), "PWR_EN")
    p.draw_rect(fitz.Rect(90, 130, 240, 240))
    p = doc.new_page(width=600, height=500)
    p.insert_text((40, 40), "BMC I2C DEBUG", fontsize=20)
    p.insert_text((220, 200), "U23")
    p.insert_text((220, 220), "PCA9548")
    p.insert_text((260, 240), "BMC_I2C_SDA")
    p.insert_text((260, 260), "BMC_I2C_SCL")
    p.insert_text((500, 440), "UNRELATED_NET")
    p.draw_rect(fitz.Rect(200, 180, 330, 280))
    p.draw_line((330, 240), (420, 240))
    # Font/style-split label on one PDF text line.
    p.insert_text((60, 350), "BMC_", fontsize=11)
    width = fitz.get_text_length("BMC_", fontsize=11)
    p.insert_text((60+width, 350), "RESET_N", fontsize=11, fontname="hebo")
    p = doc.new_page(width=600, height=500)
    p.insert_text((40, 40), "ROTATED INTERFACE", fontsize=20)
    p.insert_text((180, 300), "U77", rotate=90)
    p.insert_text((210, 300), "CPU_I2C_SDA", rotate=90)
    p.set_rotation(90)
    # Decoys exercise distinct-term coverage over repetition.
    for decoy in range(6):
        p = doc.new_page(width=600, height=500)
        p.insert_text((40, 40), f"OTHER BMC SHEET {decoy}", fontsize=20)
        for i in range(12):
            p.insert_text((80, 80+i*22), "BMC")
    doc.set_metadata({"title": "Synthetic schematic regression fixture"})
    doc.save(path)
    doc.close()
    return Path(path)
