#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Recreate original SFW inputs with existing Pillow; not needed to build the catalog."""
from pathlib import Path
from PIL import Image, ImageDraw
root=Path(__file__).resolve().parent
base=Image.new('RGB',(320,200)); p=base.load()
for y in range(200):
 for x in range(320): p[x,y]=(x*255//319,y*255//199,55)
d=ImageDraw.Draw(base)
d.rectangle((0,0,49,49),fill='#ff3030'); d.text((6,16),'LEFT',fill='white')
d.rectangle((270,140,319,199),fill='#2464ff'); d.text((275,165),'RIGHT',fill='white')
d.polygon([(120,35),(160,10),(180,80)],fill='#ffdb30'); d.text((128,100),'CENTER',fill='black')
base.save(root/'rr-starter-base.png')
overlay=Image.new('RGB',(80,60),'#20e0b0'); d=ImageDraw.Draw(overlay); d.rectangle((0,0,12,59),fill='#202060'); d.ellipse((40,5,70,35),fill='#e020a0');d.text((18,42),'PASTE',fill='black');overlay.save(root/'rr-starter-overlay.png')
