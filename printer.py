from PIL import Image
import numpy as np
import sys
import time
import serial

from fonts.draft import f_draft
from fonts.correspondence import f_correspondence
from fonts.nlq import f_nlq

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


def chrono():
   n=time.time()
   return n 


def printChrono(s,c):
   n=time.time()
   r=n-c
   print(s,r)


def diffChrono(c):
   n=time.time()-c
   return n


def rescale(f,s):
   return np.asarray(np.kron(np.array(f), np.ones((s,int(s)))),dtype=np.int8)



class ImageWriter:
   def __init__(self,name,width,height):
      self.name=name
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
      self.buffer = bytearray(self.buffer_size)
      self.buffer_ptr = 0
      self.buffer_read_ptr = 0

      self.head["column"]=0
      self.head["line"]=0
      self.dot["column"]=0
      self.dot["line"]=0
      self.dot["lineFeedSpacing"]=24*self.imagescale*ref_font_upscale
     
      self.current_font=f_draft 
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

      self.hres={
         "Extended": [ 9, 72, 576, 80/72 ],
         "Pica" : [ 10, 80, 640, 1 ], # ref 160 dpi
         "Elite" : [ 12, 96, 768, 80/96 ],
         "Semicondensed" : [ 13.4, 107, 856, 80/107 ],
         "Condensed" : [ 15, 120, 960, 80/120 ],
         "Ultracondensed" : [ 17, 136, 1088, 80/136 ],
         "PicaP" : [ -1, 144, 1152, 1 ],
         "EliteP" : [ -1, 160, 1280, 0.9 ]
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

      self.calc_im_size()
      self.im=[]
      #cself.im.append(Image.new(mode="RGBA", size=(self.il, self.ic),color=(255,255,255,255))) # white page
      self.im.append(Image.new(mode="RGB", size=(self.il, self.ic),color=(255,255,255))) # white page


   def calc_im_size(self):
      self.sl=ref_font_upscale*self.imagescale
      self.sc=ref_font_upscale*self.imagescale
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


   def lineFeed(self,lines):
      print(self.page)
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
      if car==chr(13):
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
      if car in range(32,126) or car in range(192,223):
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
            self.doit["column"]=self.left_margin
 

   def do_esc_cmd(self):
      c=self.get_buffer_next_car()
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
         if c==ord('0') or c==ord('m'):
            self.current_font=f_correspondence
         elif c==ord('1'):
            self.current_font=f_draft
         elif c==ord('2') or c==ord('M'):
            self.current_font=f_nlq
      elif c==ord('Z'):
         c=self.get_buffer_next_car()
         if c==0:
            c=self.get_buffer_next_car()
            if c==ord(' '):
               self._8bits=True
            elif c==1:
               self.zeroslashed=False
         elif c>=0 and c<=6:
            _c=c
            c=self.get_buffer_next_car()
            if c==0:
               c=self.get_buffer_next_car()
               if c==27:
                  c=self.get_buffer_next_car()
                  if c==ord('D'):
                     c=self.get_buffer_next_car()
                     if c>=0 and c<=6:
                        __c=c
                        c=self.get_buffer_next_car()
                        if c==0:
                           self.alt=self.alt_from_code[str(_c)+"_"+str(__c)]
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
         elif c==7:
            c=self.get_buffer_next_car()
            if c==0:
               self.alt="sp"
      elif c==ord('&'):
         self.mouse_on=True
      elif c==ord('$'):
         self.mouse_on=False
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

      
   def drawchar(self,x,y,couleur,f,_car,s_w,s_h):

      if DEBUG: c=chrono()

      x=int(x)
      y=int(y)
      _car=ord(_car)

      _s_w=s_w
      _s_h=s_h
      if self.doublewide==True:
         _s_w=s_w*2
      if self.mouse_on==True:
         _car=_car+128
      if self._8bits==False:
         _car=_car & 0x7F
      if self.halfheight==True:
         _s_h=s_h*0.5
      if self.s_script != 0:
         _s_w=s_w*0.5
         _s_h=s_h*0.5
      _rc=self.imagescale*self.fontrefc*ref_font_upscale*_s_w
      _rl=self.imagescale*self.fontrefl*ref_font_upscale*_s_h
      rc=int(_rc)
      rl=int(_rl)
      dl=int(self.dl)

      car=-1
      prefix=""
      suffix=""
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
         
      if not scar in self.cache[cache_font_id]:
         try:
            _f=rescale(font,f["upscale"])
         except Exception as error:
            if DEBUG: print("rescale:",chr(_car),car,scar,error)
            pass
         _fc=len(_f[0])
         _fl=len(_f)
         imgcar = Image.new(mode="RGBA", size=(_fc, _fl), color=(255,255,255,0))
         for c in range(_fc): # colonne
            for l in range(_fl): # ligne
               try:
                  if(_f[l][c]):
                     imgcar.putpixel((c,l), (0,0,0,255))
               except:
                  pass
         if self.underlined:
            _u=_f=rescale(f["underline"],f["upscale"])
            if DEBUG: print(_u,len(_u),len(_u[0]))
            for c in range(len(_u[0])):
               for l in range(len(_u)):
                  if(_u[l][c]):
                     imgcar.putpixel((c,_fl-l-1), (0,0,0,255))
         self.cache[cache_font_id][scar] = imgcar.resize((rc, rl), Image.Resampling.LANCZOS)

      imgcar=self.cache[cache_font_id][scar]

      yd=0
      if self.halfheight:
         yd=4*ref_font_upscale 
      if self.s_script<0:
         yd=7*ref_font_upscale 

      self.im[self.page].paste(imgcar,(x+dl,y+yd),imgcar) # see http://effbot.org/imagingbook/image.htm. second imgcar used as mask

      if self.boldface:
         self.im[self.page].paste(imgcar,(x+dl+1*ref_font_upscale,y+yd),imgcar)
      if chr(_car)=='0' and self.zeroslashed:
         self.drawchar(x,y,couleur,f,'/',_s_w,_s_h)
      return _rc,_rl


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
      f=self.current_font
      s_w,s_h=self.getwh(self.current_size)

      x,y=self.drawchar(self.dot["column"],self.dot["line"],color,f,_car,s_w,s_h)
      if x!=None and y!=None:
         if self.dot["column"]+x+0.05 < self.il:
            self.head["column"]=self.head["column"]+1
            self.dot["column"]=self.dot["column"]+x
         else:
            self.head["column"]=self.left_margin
            self.head["line"]=self.head["line"]+1
            self.dot["column"]=0
            self.lineFeed(1)


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

 
printer = ImageWriter("p1",21/2.54,29.7/2.54)
#printer.crlf=False
#printer.serial_to_buffer("/dev/tty.usbserial-14330",\
#                         9600,\
#                         serial.PARITY_NONE,\
#                         serial.STOPBITS_ONE,serial.SEVENBITS)
printer.add_to_buffer(chr(27))
printer.add_to_buffer("a")
printer.add_to_buffer("1")
for i in range(80):
   printer.add_str_to_buffer(str(i%10))
printer.add_to_buffer(chr(13))
for i in range(80):
   printer.add_str_to_buffer(str(i))
   printer.add_to_buffer(chr(13))

printer.add_to_buffer(chr(27))
printer.add_to_buffer("!")
printer.add_to_buffer("t")
printer.add_to_buffer("o")
printer.add_to_buffer(chr(14))
printer.add_to_buffer("t")
printer.add_to_buffer("o")
printer.add_to_buffer(chr(15))
printer.add_to_buffer(chr(27))
printer.add_to_buffer('"')
printer.add_to_buffer(chr(27))
printer.add_to_buffer("a")
printer.add_to_buffer("2")
printer.add_to_buffer("T")
printer.add_to_buffer(chr(27))
printer.add_to_buffer("N")
printer.add_to_buffer("O")
printer.add_to_buffer("T")
printer.add_to_buffer("O")
printer.add_to_buffer(chr(27))
printer.add_to_buffer("a")
printer.add_to_buffer("1")
printer.add_to_buffer(chr(27))
printer.add_to_buffer("&")
printer.add_to_buffer("A")
printer.add_to_buffer(chr(27))
printer.add_to_buffer("$")
printer.add_to_buffer(chr(192))
printer.add_to_buffer(chr(27))
printer.add_to_buffer('Z')
printer.add_to_buffer(chr(0))
printer.add_to_buffer(' ')
printer.add_to_buffer(chr(192))
printer.add_to_buffer(chr(27))
printer.add_to_buffer('D')
printer.add_to_buffer(chr(0))
printer.add_to_buffer(' ')
printer.add_to_buffer(chr(192))

print("us")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('Z')
printer.add_to_buffer(chr(7))
printer.add_to_buffer(chr(0))
printer.add_to_buffer('@')

print("sp")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('D')
printer.add_to_buffer(chr(7))
printer.add_to_buffer(chr(0))
printer.add_to_buffer('@')

# Undelined
printer.add_to_buffer(chr(27))
printer.add_to_buffer('X')
# Half-Height
printer.add_to_buffer(chr(27))
printer.add_to_buffer('w')
printer.add_str_to_buffer("Une longue cha|ne")
# end Half-Height
printer.add_to_buffer(chr(27))
printer.add_to_buffer('W')

printer.add_to_buffer(chr(27))
printer.add_to_buffer("a")
printer.add_to_buffer("0")

print("fr")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('Z')
printer.add_to_buffer(chr(1))
printer.add_to_buffer(chr(0))
printer.add_to_buffer(chr(27))
printer.add_to_buffer('D')
printer.add_to_buffer(chr(6))
printer.add_to_buffer(chr(0))
printer.add_to_buffer('@')

printer.add_str_to_buffer(" de charact"+chr(125)+"res")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('x')
printer.add_str_to_buffer("en haut")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('y')
printer.add_str_to_buffer("en bas")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('z')
printer.add_str_to_buffer("normal")
printer.add_str_to_buffer("0000")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('D')
printer.add_to_buffer(chr(0))
printer.add_to_buffer(chr(1))
printer.add_str_to_buffer("0000")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('Y')
printer.add_to_buffer(chr(13))
printer.add_to_buffer(chr(27))
printer.add_to_buffer('L')
printer.add_to_buffer('0')
printer.add_to_buffer('1')
printer.add_to_buffer('2')
printer.add_str_to_buffer("0000")
printer.add_to_buffer(chr(13))
printer.add_str_to_buffer("0000")
printer.add_to_buffer(chr(27))
printer.add_to_buffer('L')
printer.add_to_buffer('0')
printer.add_to_buffer('0')
printer.add_to_buffer('0')
printer.add_str_to_buffer("0000")
printer.add_to_buffer(chr(13))
printer.add_str_to_buffer("0000")
printer.add_to_buffer('o')
printer.add_to_buffer(chr(8))
printer.add_to_buffer(chr(8))
printer.add_to_buffer(chr(8))
printer.add_to_buffer('X')
printer.add_to_buffer('X')
printer.add_to_buffer('X')
printer.add_to_buffer(chr(24))
printer.add_str_to_buffer("0000")


printer.do_buffer()

printer.im[0].save("test.pdf",save_all=True,append=False,dpi=(160*printer.sc,144*printer.sl),append_images=printer.im[1:])

sys.exit()
