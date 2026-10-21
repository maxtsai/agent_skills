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


def make_review_pdf(path):
    """Synthetic cross-board candidates plus electrical traps for visual review."""
    doc = fitz.open()
    for title, connector, labels in [
            ("BOARD LEFT", "J1", [("A13", "PG_HOST"), ("B48", "DATA_HOST"), ("A1", "SUPPLY_HOST")]),
            ("BOARD RIGHT", "J9", [("A13", "STBY_GPIO"), ("B48", "RMII_DATA0"), ("A1", "SUPPLY_CARD")])]:
        p = doc.new_page(width=600, height=500)
        p.insert_text((40, 40), title, fontsize=20)
        p.insert_text((400, 65), connector)
        p.draw_rect(fitz.Rect(385, 75, 460, 205))
        for i, (pin, net) in enumerate(labels):
            y = 100+i*40
            p.insert_text((400, y), pin)
            p.insert_text((80, y), net)
            p.draw_line((190, y-3), (385, y-3))
        # A1 must not match A13 or OA13. A second connector needs bbox scoping.
        p.insert_text((400, 220), "OA13")
        p.insert_text((490, 100), "DECOY_NET")  # same row, no connecting wire
        p.insert_text((400, 275), "J2")
        p.insert_text((400, 300), "A13")
        p.insert_text((80, 300), "OTHER_CONNECTOR_NET")
        # This pin exists as rotated text; no horizontal net suggestion is safe.
        p.insert_text((510, 380), "B26", rotate=90)
        p.insert_text((80, 380), "ROTATED_DECOY_NET")
    p = doc.new_page(width=600, height=500)
    p.insert_text((30, 35), "ELECTRICAL REVIEW TRAPS", fontsize=20)
    p.insert_text((30, 65), "R1: follow the resistor endpoint, not the nearby rail text.")
    p.insert_text((40, 100), "PG_REVIEW")
    p.draw_line((125, 97), (240, 97))
    p.draw_line((240, 97), (240, 140))
    p.draw_rect(fitz.Rect(234, 140, 246, 175))
    p.insert_text((260, 155), "R1")
    p.insert_text((260, 173), "4.7k")
    p.draw_line((240, 175), (240, 205))
    for width, y in [(25, 205), (16, 211), (7, 217)]:
        p.draw_line((240-width/2, y), (240+width/2, y))
    p.insert_text((265, 213), "GND")
    p.insert_text((190, 126), "VCC_3V3")  # nearby text, no supply wire
    p.insert_text((30, 255), "R2")
    p.insert_text((60, 255), "NO STUFF")
    p.insert_text((160, 255), "10k")
    p.draw_line((190, 280), (300, 280))
    p.draw_line((245, 250), (245, 315))  # crossing, deliberately no junction dot
    p.insert_text((30, 330), "GPIO_REVIEW")
    p.insert_text((190, 330), "DIRECTION UNKNOWN / PINMUX REQUIRED")
    p.insert_text((30, 375), "RAIL_HOST_3V3")
    p.insert_text((250, 375), "RAIL_CARD_3V3")
    p.insert_text((30, 420), "RST_REVIEW_N")
    p.insert_text((250, 420), "12V_CARD")
    p.insert_text((30, 455), "No waveform: reset assertion alone does not prove a 12V power cycle.")
    doc.save(path)
    doc.close()
    return Path(path)
