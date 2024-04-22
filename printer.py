from PIL import Image
import numpy as np
import sys
import time
import serial

from fonts.draft import f_draft
from fonts.correspondence import f_correspondence
from fonts.correspondence import f_correspondenceP
from fonts.nlq import f_nlq
from fonts.nlq import f_nlqP

DEBUG=False

ref_font_upscale=2
f_draft["upscale"]=ref_font_upscale
f_correspondence["upscale"]=ref_font_upscale
f_nlq["upscale"]=int(ref_font_upscale/2)

# resolution vertical normal : 72 dpi
# resolution vertical maximum : 144 dpi
# A4 = 29,7*21 => 11,7*8,27 => 1684 * 1191 ~ 2 Mo => 15 Mo (256 couleurs)
# max dot per line : 1280


def binary(num, length=8):
    return format(num, '#0{}b'.format(length + 2))


class ImageWriter:
   def __init__(self,name,width,height):
      self.name = name
      self.width = width
      self.height = height

      # image scaling for better quality
      self.imagescale=1
      
      # font size reference
      self.fontrefc=16
      self.fontrefl=18
      
      self.line_dpi = 144 # /!\ vertical is max 144 dpi ... vs column is 160 dpi
      self.column_dpi = 160
      self.max_page_width = 8 # inch
      self.head={}
      self.dot={}
      self.cache={}

      self.page = 0
      self.buffer_size=2048
      # self.buffer_size=32768 # 32K Memory option
      self.buffer = bytearray(self.buffer_size)
      self.buffer_ptr = 0
      self.buffer_read_ptr = 0

      self.head["column"]=0
      self.head["line"]=0
      self.dot["column"]=0
      self.dot["line"]=0
      self.dot["lineFeedSpacing"]=24*self.imagescale*ref_font_upscale
 
      self.current_font="draft"
#      self.current_font=f_draft 
      self.current_size="Pica"
      self.alt="us"
      self.underlined=False
      self.boldface=False
      self.doublewide=False
      self.mouse_on=False
      self._8bits=False
      self.halfheight=False
      self.s_script=0
      self.zeroslashed=False
      self.left_margin=0
      self.crlf=True
      self.tabs=[]

      self.il=0
      self.ic=0
      self.sl=0
      self.sc=0
      self.max_page_with_dpi=0

      self.hres={
         "Extended": [ 9, 72, 576, 80/72 ],
         "Pica" : [ 10, 80, 640, 1 ], # ref 160 dpi
         "Elite" : [ 12, 96, 768, 80/96 ],
         "Semicondensed" : [ 13.4, 107, 856, 80/107 ],
         "Condensed" : [ 15, 120, 960, 80/120 ],
         "Ultracondensed" : [ 17, 136, 1088, 80/136 ],
         "PicaP" : [ 9, 144, 1152, 1 ],
         "EliteP" : [ 10, 160, 1280, 144/160 ]
      }

      self.font_combination = {
         "draft": {
            "standard": f_draft,
            "halfsize": f_correspondence,
            "proportional": f_correspondenceP 
         },
         "correspondence": {
            "standard": f_correspondence,
            "halfsize": f_correspondence,
            "proportional": f_correspondenceP 
         },
         "nlq": {
            "standard": f_nlq,
            "halfsize": f_correspondence,
            "proportional": f_nlqP
         },
      }

      self.alt_from_code={
         "7_0":"us",
         "6_1":"it",
         "5_2":"da",
         "4_3":"uk",
         "3_4":"de",
         "2_5":"sw",
         "1_6":"fr",
         "0_7":"sp",
      }
      self.esc_z = -1

      self.calc_im_size()
      self.im=[]
      #cself.im.append(Image.new(mode="RGBA", size=(self.il, self.ic),color=(255,255,255,255))) # white page
      self.im.append(Image.new(mode="RGB", size=(self.il, self.ic),color=(255,255,255))) # white page


   def rescale(self,f,s):
      return np.asarray(np.kron(np.array(f), np.ones((s,int(s)))),dtype=np.int8)


   def get_font(self,f):
      _f = self.font_combination[f]
      if self.halfheight==True:
         return _f["halfsize"]
      elif self.current_size=="PicaP" or self.current_size=="EliteP":
         return _f["proportional"]
      else:
         return _f["standard"]

   def calc_im_size(self):
      self.sl=ref_font_upscale*self.imagescale
      self.sc=ref_font_upscale*self.imagescale
      self.max_page_with_dpi=round(self.max_page_width*self.column_dpi*self.sl)
      self.il=round(self.column_dpi*self.width*self.sl)
      self.ic=round(self.line_dpi*self.height*self.sc)
      self.dl=(self.width-8)*self.column_dpi/2*self.sl
      if self.dl<0: self.dl=0
   
   def setCurrentFont(self,current_font):
      self.current_font=current_font


   def setCurrentSize(self,current_size):
      self.current_size=current_size


   def setLineSpacing(self,lines):
      self.dot["lineFeedSpacing"]=round(144/lines*self.imagescale)
      

   def setLineFeedSpacing(self,dotPerInch):
      self.dot["lineFeedSpacing"]=dotPerInch*self.imagescale


   def formFeed(self):
      self.dot["column"]=self.left_margin
      self.dot["line"]=0
      self.im.append(Image.new(mode="RGB", size=(self.il, self.ic),color=(255,255,255)))
      self.page=self.page+1


   def lineFeed(self,lines):
      self.dot["line"]=self.dot["line"]+lines*self.dot["lineFeedSpacing"]
      if self.dot["line"]<0: self.dot["line"]=0
      if self.dot["line"]+self.fontrefl*ref_font_upscale > self.ic:
         self.dot["line"]=0
         self.im.append(Image.new(mode="RGB", size=(self.il, self.ic),color=(255,255,255)))
         self.page=self.page+1


   def add_to_buffer(self,car):
      if car==chr(10):
         self.do_buffer()
         self.lineFeed(1)
      elif car==chr(12):
         self.do_buffer()
         self.formFeed()
      elif car==chr(13):
         self.do_buffer()
         self.CR(self.crlf)
      elif car==chr(24): # ctrl-X
         self.buffer_read_ptr=0
         self.buffer_ptr=0
      elif self.buffer_ptr < self.buffer_size:
         self.buffer[self.buffer_ptr]=ord(car)
         self.buffer_ptr=self.buffer_ptr+1
         return True
      else:
         return None


   def add_str_to_buffer(self,s):
      for c in s:
         self.add_to_buffer(c)

 
   def is_printable(self,car):
      if car in range(32,127) or car in range(192,224):
         return True
      else:
         return False


   def find_next_tab(self):
      print("to do")


   def get_buffer_next_car(self):
      c=self.buffer[self.buffer_read_ptr]
      self.buffer_read_ptr=self.buffer_read_ptr+1
      return c


   def get_buffer_next_number_as_string(self,nb_chars=3):
      num=""
      for i in range(nb_chars):
         num=num+chr(self.buffer[self.buffer_read_ptr])
         self.buffer_read_ptr=self.buffer_read_ptr+1
      return num

 
   def get_buffer_next_number_as_int(self):
      return int(self.get_buffer_next_number_as_string())


   def do_ctrl_cmd(self,c):
      if c==14:
         self.doublewide=True
      elif c==15:
         self.doublewide=False
      elif c==8:
         s_w,s_h=self.getwh(self.current_size) 
         _rc=self.imagescale*self.fontrefc*ref_font_upscale*s_w
         self.dot["column"]=self.dot["column"]-_rc
         if self.dot["column"]<self.left_margin:
            self.dot["column"]=self.left_margin
 

   def do_esc_cmd(self):
      c=self.get_buffer_next_car()
      if c!=ord('D') and self.esc_z !=-1: # reset prev CTRL-Z for font selection
         self.esc_z=-1
      if c==ord('X'): # Underlined
         self.underlined=True
      elif c==ord('Y'): # Ununderlined
         self.underlined=False
      elif c==ord('!'): # Boldface 
         self.boldface=True
      elif c==ord('"'): # Unboldface
         self.boldface=False
      elif c==ord('a'):
         c=self.get_buffer_next_car()
         if c==ord('0'):
            # self.current_font=f_correspondence
            self.current_font="correspondence"
         elif c==ord('1'):
            self.current_font="draft"
         elif c==ord('2'):
            self.current_font="nlq"
      elif c==ord('Z'):
         c=self.get_buffer_next_car()
         if c==0:
            c=self.get_buffer_next_car()
            if c==ord(' '):
               self._8bits=True
            elif c==1:
               self.zeroslashed=False
         elif c>=1 and c<=6: # can be a font selection sequence
            _c=c 
            c=self.get_buffer_next_car()
            if c==0: # it is first characters of font sequence
               self.esc_z = _c # keep value in memory
            else:
               self.esc_z = -1
         elif c==7:
            c=self.get_buffer_next_car()
            if c==0:
               self.alt="us"
      elif c==ord('D'):
         c=self.get_buffer_next_car()
         if c==0:
            c=self.get_buffer_next_car()
            if c==ord(' '):
               self._8bits=False
            elif c==1:
               self.zeroslashed=True 
         if c>=1 and c<=6 and self.esc_z != -1: # second part of font sequence 
            __c=c
            c=self.get_buffer_next_car()
            if c==0: # validation of font sequence
               self.alt=self.alt_from_code[str(self.esc_z)+"_"+str(__c)]
            self.esc_z=-1
         elif c==7:
            c=self.get_buffer_next_car()
            if c==0:
               self.alt="sp"
      elif c==ord('&'):
         self.mouse_on=True
      elif c==ord('$'):
         self.mouse_on=False
      elif c==ord('m'):
         self.current_font="correspondence"
      elif c==ord('M'):
         self.current_font="nlq"
      elif c==ord('n'):
         self.current_size="Extended"
      elif c==ord('N'):
         self.current_size="Pica"
      elif c==ord('E'):
         self.current_size="Elite"
      elif c==ord('e'):
         self.current_size="Semicondensed"
      elif c==ord('q'):
         self.current_size="Condensed"
      elif c==ord('Q'):
         self.current_size="Ultracondensed"
      elif c==ord('p'):
         self.current_size="PicaP"
      elif c==ord('P'):
         self.current_size="EliteP"
      elif c==ord('w'):
         self.halfheight=True
      elif c==ord('W'):
         self.halfheight=False
      elif c==ord('x'):
         self.s_script=1
      elif c==ord('y'):
         self.s_script=-1
      elif c==ord('z'):
         self.s_script=0
      elif c==ord('L'):
         l=self.get_buffer_next_number_as_int()
         self.left_margin=self.il/self.hres[self.current_size][1]*l
         if self.dot["column"]<self.left_margin:
            self.dot["column"]=self.left_margin
      elif c==ord('H'):
         l=self.get_buffer_next_number_as_int(4)
         self.height=l/144
         self.calc_im_size()
      elif c==ord('>') or c==ord('<'):
         print("NOP")
      elif c==ord('1'):
         c=self.get_buffer_next_car()
         if c==ord('0'):
            self.crlf=True
         elif c==ord('1'):
            self.crlf=False
 
 
   def do_buffer(self):
      if self.buffer_ptr==0:
         return False

      _end=False
      while(not _end):
         c=self.buffer[self.buffer_read_ptr]
         self.buffer_read_ptr=self.buffer_read_ptr+1
        
         if self.is_printable(c):
            self.putchar(chr(c))
         elif c==27:
            self.do_esc_cmd()
         elif c<27:
            self.do_ctrl_cmd(c)
         if self.buffer_read_ptr>=self.buffer_ptr:
            _end=True 

      self.buffer_ptr=0
      self.buffer_read_ptr=0


   def CR(self, lf=True):
      self.dot["column"]=self.left_margin
      self.head["line"]=0
      if lf:
         self.lineFeed(1)
         self.head["line"]=self.head["line"]+1
   

   def getcharimg(self,_car,couleur,_f,s_w,s_h):

      _car=ord(_car)
      _s_w=s_w
      _s_h=s_h

      _rc=0
      _rl=0
      rc=0
      rl=0
      car=-1
      prefix=""
      suffix=""

      f = self.get_font(_f)

      if self.doublewide==True:
         _s_w=s_w*2
      if self._8bits==False:
         _car=_car & 0x7F
      if self.mouse_on==True and _car>=ord("@") and _car<=ord("_"):
         _car=_car+128
      if self.halfheight==True:
         _s_h=s_h*0.5
      if self.s_script != 0:
         _s_w=s_w*0.5
         _s_h=s_h*0.5

      try:
         car=f["alt"]["list"].index(_car)
         font=f["alt"]["font"][self.alt][car]
         prefix=self.alt+"_"
      except:
         if _car<32: return None, None
         if _car>=192 and _car<=223:
            try:
               car=_car-192
               font=f["mouse"][car]
               prefix="m_"
            except:
               return None,None
         else:
            prefix=""
            car=_car-32
            font=f["font"][car]
      if self.underlined:
         suffix="_u"

      scar=str(car)

      cache_font_id=f["name"]+"_"+prefix+str(_s_w)+"_"+str(_s_h)+suffix # name of character image in cache

      if not cache_font_id in self.cache:
         self.cache[cache_font_id]={}
         
      _fc=len(font[0])*f["upscale"]
      _fl=len(font)*f["upscale"]
      m_rescale=False
      try:
         m_rescale=f["mouse_must_rescale"]
      except:
         pass
      if _car<192 or m_rescale:
         _rc=self.imagescale*_fc*_s_w*ref_font_upscale*f["width_rescale"]
      else:
         _rc=self.imagescale*_fc*_s_w*ref_font_upscale
      _rl=self.imagescale*_fl*_s_h*ref_font_upscale

      if not scar in self.cache[cache_font_id]:
         try:
            _f=self.rescale(font,f["upscale"])
         except Exception as error:
            if DEBUG: print("rescale:",chr(_car),car,scar,error)
            pass
         imgcar = Image.new(mode="RGBA", size=(_fc, _fl), color=(255,255,255,0))
         for c in range(_fc): # colonne
            for l in range(_fl): # ligne
               try:
                  if(_f[l][c]):
                     imgcar.putpixel((c,l), (0,0,0,255))
               except:
                  pass
         self.cache[cache_font_id][scar] = imgcar.resize((int(_rc), int(_rl)), Image.Resampling.LANCZOS)

      return self.cache[cache_font_id][scar],_rc,_rl


   def draw(self,x,y,imgcar):

      yd=0
      dl=int(self.dl)

      if self.halfheight:
         yd=4*ref_font_upscale 
      if self.s_script<0:
         yd=7*ref_font_upscale 

      self.im[self.page].paste(imgcar,(x+dl,y+yd),imgcar) # see http://effbot.org/imagingbook/image.htm. second imgcar used as mask
      if self.boldface:
         self.im[self.page].paste(imgcar,(x+dl+1*ref_font_upscale,y+yd),imgcar)


   def printgr(self,x,y,b,color):
      rc=round(2*self.imagescale*ref_font_upscale)
      rl=round(16*self.imagescale*ref_font_upscale)
      for i in range(rc):
         for j in range(rl):
            try:
               self.im[self.page].putpixel((i+x,j+y),color)
            except Exception as error:
               print(error)
               pass
      return rc,rl
   def putgr(self, b, color):
      x,y=self.printgr(self.dot["column"],self.dot["line"],b,color=color)
      if x!=None and y!=None:
         if self.dot["column"]+x < self.il:
            self.dot["column"]=self.dot["column"]+x
         else:
            self.dot["column"]=0
            self.lineFeed(1)

   
   def getwh(self,size):
      s_w=self.hres[size][3] 
      return s_w,1


   def putchar(self,_car,color=(0,0,0)):
      s_w = s_h = 1
      size=self.current_size
      s_w,s_h=self.getwh(self.current_size)
      img,_rc,_rl=self.getcharimg(_car,color,self.current_font,s_w,s_h)
      if self.dot["column"]+_rc > self.max_page_with_dpi:
         self.CR(self.crlf)

      printer.draw(int(self.dot["column"]),int(self.dot["line"]),img)

      if _car=='0' and self.zeroslashed:
         img,x,x=self.getcharimg("/",color,self.current_font,s_w,s_h)
         printer.draw(int(self.dot["column"]),int(self.dot["line"]),img)

      self.dot["column"]=self.dot["column"]+_rc
      self.head["column"]=self.head["column"]+1

   def serial_to_buffer(self,port,baudrate,parity,stopbits,bytesize):
      ser = serial.Serial(port=port,\
                          baudrate=baudrate,\
                          parity=serial.PARITY_NONE,\
                          stopbits=serial.STOPBITS_ONE,\
                          bytesize=serial.SEVENBITS,\
                          timeout=0)
      while True:
         for line in ser.read():
            printer.add_to_buffer(chr(line))
            print(line,chr(line))
            if int(line)==13:
               printer.im[0].save("test.png")
      ser.close()


#
# test function
#
esc_cmd_for_alt_selection={
   "us": chr(27)+"Z"+chr(7)+chr(0),
   "it": chr(27)+"Z"+chr(6)+chr(0)+chr(27)+"D"+chr(1)+chr(0),
   "da": chr(27)+"Z"+chr(5)+chr(0)+chr(27)+"D"+chr(2)+chr(0),
   "uk": chr(27)+"Z"+chr(4)+chr(0)+chr(27)+"D"+chr(3)+chr(0),
   "de": chr(27)+"Z"+chr(3)+chr(0)+chr(27)+"D"+chr(4)+chr(0),
   "sw": chr(27)+"Z"+chr(2)+chr(0)+chr(27)+"D"+chr(5)+chr(0),
   "fr": chr(27)+"Z"+chr(1)+chr(0)+chr(27)+"D"+chr(6)+chr(0),
   "sp": chr(27)+"D"+chr(7)+chr(0)
}

esc_for_font_selection={
   "correspondence": chr(27)+"a0",
   "draft": chr(27)+"a1",
   "nlq": chr(27)+"a2",
   "correspondence_2": chr(27)+"m",
   "nlq_2": chr(27)+"M",
}

esc_8bits = {
   "off" : chr(27)+"D"+chr(0)+" ",
   "on" : chr(27)+"Z"+chr(0)+" "
}

esc_mouse_low = {
   "on" : chr(27)+"&",
   "off" : chr(27)+"$"
}

esc_char_attr = {
   "underline_on"     : chr(27)+"X", # pas testé
   "underline_off"    : chr(27)+"Y", # pas testé
   "bold_on"          : chr(27)+"!",
   "bold_off"         : chr(27)+'"',
   "double_width_on"  : chr(ord("N")-ord("@")),
   "double_width_off" : chr(ord("O")-ord("@")),
   "half_height_on"   : chr(27)+"w",
   "half_height_off"  : chr(27)+"W",
   "super_on"         : chr(27)+"x",
   "sub_on"           : chr(27)+"y",
   "super_sub_off"    : chr(27)+"z",
   "zero_s"           : chr(27)+"D"+chr(0)+chr(1),
   "zero_u"           : chr(27)+"Z"+chr(0)+chr(1)
}

esc_pitch = {
   "Extended"       : chr(27)+"n",
   "Pica"           : chr(27)+"N",
   "Elite"          : chr(27)+"E",
   "Semicondensed"  : chr(27)+"e",
   "Condensed"      : chr(27)+"q",
   "UltraCondensed" : chr(27)+"Q",
   "PicaP"          : chr(27)+"p",
   "EliteP"         : chr(27)+"P"
}

def print_pitch(printer,f):
   printer.add_str_to_buffer(esc_for_font_selection[f])
   for i in esc_pitch:
      printer.add_str_to_buffer(esc_pitch[i])
      printer.add_str_to_buffer("ABCDEFGH012345678")
      printer.add_to_buffer(chr(13))


def print_font(printer,f):
   printer.alt="us"
   printer.add_str_to_buffer(esc_for_font_selection[f])
#   printer.doublewide=True
   printer.add_str_to_buffer(esc_char_attr["double_width_on"])
   printer.add_str_to_buffer(f+" "+printer.current_size+chr(13)+chr(13))
#   printer.doublewide=False
   printer.add_str_to_buffer(esc_char_attr["double_width_off"])
   for i in range(32,127):
      printer.add_str_to_buffer(chr(i))
   printer.add_to_buffer(chr(13))

   printer.add_str_to_buffer(esc_8bits["off"])
   printer.add_str_to_buffer(esc_mouse_low["on"])
   printer.add_str_to_buffer("Mouse: ")
   for i in range(192,234):
      printer.add_str_to_buffer(chr(i))
   printer.add_to_buffer(chr(13))

   printer.add_str_to_buffer(esc_8bits["on"])
   printer.add_str_to_buffer(esc_mouse_low["off"])
   printer.add_str_to_buffer("Mouse: ")
   for i in range(192,234):
      printer.add_str_to_buffer(chr(i))

   printer.add_to_buffer(chr(13))
   for i in ["us", "it", "da", "uk", "de", "sw", "fr", "sp"]:
      printer.add_str_to_buffer(esc_cmd_for_alt_selection[i])
      printer.add_str_to_buffer(i+": ")
      for j in [35,64,91,92,93,96,123,124,125,126]:
         printer.add_str_to_buffer(chr(j))
      printer.add_to_buffer(chr(13))
   printer.add_to_buffer(chr(13))


#
# end test function
#
 
#printer = ImageWriter("p1",21/2.54,29.7/2.54)
printer = ImageWriter("p1",8.5,12)

#printer.crlf=False
printer._8bits=True

printer.setCurrentSize("Elite")
print_font(printer,"draft")
print_font(printer,"correspondence")
printer.setCurrentSize("EliteP")
print_font(printer,"correspondence")
printer.setCurrentSize("Elite")
print_font(printer,"nlq")
printer.setCurrentSize("EliteP")
print_font(printer,"nlq")
printer.boldface=True
printer.add_to_buffer(chr(12))

printer.setCurrentSize("Pica")
print_font(printer,"draft")
print_font(printer,"correspondence_2")
printer.setCurrentSize("PicaP")
print_font(printer,"correspondence_2")
printer.setCurrentSize("Pica")
print_font(printer,"nlq_2")
printer.setCurrentSize("PicaP")
print_font(printer,"nlq_2")

printer.add_str_to_buffer(esc_char_attr["bold_on"])
printer.add_to_buffer(chr(12))
printer.setCurrentSize("Ultracondensed")
print_font(printer,"draft")
print_font(printer,"correspondence")
print_font(printer,"nlq")
printer.add_str_to_buffer(esc_char_attr["bold_off"])
printer.add_to_buffer(chr(12))

printer.add_str_to_buffer(esc_pitch["Pica"])
printer.add_str_to_buffer(esc_char_attr["super_on"])
printer.add_str_to_buffer("ABCDEFGH")
printer.add_str_to_buffer(esc_char_attr["super_sub_off"])
printer.add_str_to_buffer("ABCDEFGH")
printer.add_str_to_buffer(esc_char_attr["sub_on"])
printer.add_str_to_buffer("ABCDEFGH"+chr(13))
printer.add_str_to_buffer(esc_char_attr["super_sub_off"])

printer.add_str_to_buffer(esc_char_attr["half_height_on"])
printer.add_str_to_buffer(esc_for_font_selection["draft"])
printer.add_str_to_buffer("ABCDEFGH"+chr(13))
printer.add_str_to_buffer(esc_for_font_selection["correspondence"])
printer.add_str_to_buffer(esc_char_attr["bold_on"])
printer.add_str_to_buffer("ABCDEFGH"+chr(13))
printer.add_str_to_buffer(esc_char_attr["bold_off"])
printer.add_str_to_buffer(esc_for_font_selection["nlq"])
printer.add_str_to_buffer(esc_char_attr["double_width_on"])
printer.add_str_to_buffer("ABCDEFGH"+chr(13))
printer.add_str_to_buffer(esc_char_attr["double_width_off"])
printer.add_str_to_buffer(esc_char_attr["half_height_off"])
printer.add_str_to_buffer(esc_char_attr["zero_s"])
printer.add_str_to_buffer("00000"+chr(13))
printer.add_str_to_buffer(esc_char_attr["zero_u"])
printer.add_str_to_buffer("00000"+chr(13))

print_pitch(printer,"nlq")
printer.add_to_buffer(chr(12))

printer.setCurrentSize("Pica")
printer.setCurrentFont("nlq")
for i in range(80):
   printer.add_str_to_buffer(str(i%10))
printer.add_to_buffer(chr(13))
for i in range(1,72):
   printer.add_str_to_buffer(str(i))
   printer.add_to_buffer(chr(13))
printer.add_to_buffer(chr(13))

printer.do_buffer()

printer.im[0].save("test.pdf",save_all=True,append=False,dpi=(160*printer.sc,144*printer.sl),append_images=printer.im[1:])

sys.exit(0)
