"""Diagrams for the 'detecting SOLID' blog post. Output -> static/images/ (solid-*.png)."""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "static", "images")
os.makedirs(OUT, exist_ok=True)

PAPER=(251,247,238); CARD=(255,255,255); INK=(43,58,66); MUTED=(124,142,148)
LINE=(224,216,199); BLUE=(38,139,210); BLUE_BG=(224,240,250)
GREEN=(110,135,0); GREEN_BG=(233,239,206); TEAL=(42,161,152)
RED=(200,45,42); RED_BG=(250,226,224); ORANGE=(190,80,30); AMBER=(181,137,0); AMBER_BG=(247,239,214)

F="C:/Windows/Fonts"
def font(n,s): return ImageFont.truetype(os.path.join(F,n),s)
H1=font("segoeuib.ttf",42); H2=font("segoeuib.ttf",30); H3=font("segoeuib.ttf",25)
B=font("segoeui.ttf",23); BB=font("segoeuib.ttf",23); SM=font("segoeui.ttf",19); SMB=font("segoeuib.ttf",19)
MONO=font("consola.ttf",19); MONOB=font("consolab.ttf",20); TINY=font("segoeui.ttf",16); TINYB=font("segoeuib.ttf",16)

def cv(w,h,bg=PAPER):
    im=Image.new("RGB",(w,h),bg); return im,ImageDraw.Draw(im)
def rr(d,box,r,fill=None,outline=None,width=2): d.rounded_rectangle(box,radius=r,fill=fill,outline=outline,width=width)
def ctr(d,cx,y,t,f,fill=INK):
    w=d.textlength(t,font=f); d.text((cx-w/2,y),t,font=f,fill=fill)
def chip(d,x,y,t,f,fg,bg,px=12,py=6):
    w=d.textlength(t,font=f); rr(d,[x,y,x+w+2*px,y+f.size+2*py],9,fill=bg); d.text((x+px,y+py),t,font=f,fill=fg)
    return x+w+2*px
def wrap(d,t,f,maxw):
    words=t.split(); lines=[]; cur=""
    for w in words:
        test=(cur+" "+w).strip()
        if d.textlength(test,font=f)<=maxw: cur=test
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines
def para(d,x,y,t,f,maxw,fill=INK,lh=None):
    lh=lh or f.size+6
    for ln in wrap(d,t,f,maxw): d.text((x,y),ln,font=f,fill=fill); y+=lh
    return y
def arrow(d,x1,y1,x2,y2,color=MUTED,width=3,head=12):
    import math; d.line([(x1,y1),(x2,y2)],fill=color,width=width)
    a=math.atan2(y2-y1,x2-x1)
    for s in(-0.5,0.5): d.line([(x2,y2),(x2-head*math.cos(a+s),y2-head*math.sin(a+s))],fill=color,width=width)


# 1. COVER
def cover():
    W,H=1280,600; im,d=cv(W,H); d.rectangle([0,0,W,6],fill=BLUE)
    ctr(d,W/2,52,"Is this code already SOLID?",H1,INK)
    ctr(d,W/2,112,"A detection method you can run on any codebase, even legacy",B,MUTED)
    cards=[("S","changes for\ntwo reasons?",RED,"Single Resp."),("O","edit to add\na new case?",AMBER,"Open/Closed"),
           ("L","swap breaks\nthe contract?",BLUE,"Liskov"),("I","depends on\nunused parts?",TEAL,"Interface Seg."),
           ("D","logic names a\nconcrete detail?",GREEN,"Dependency Inv.")]
    cw,gap=210,24; total=5*cw+4*gap; x0=(W-total)//2; y=210; ch=300
    for i,(L,q,col,nm) in enumerate(cards):
        x=x0+i*(cw+gap); rr(d,[x,y,x+cw,y+ch],16,fill=CARD,outline=col,width=3)
        ctr(d,x+cw/2,y+26,L,font("segoeuib.ttf",78),fill=col)
        d.line([(x+30,y+128),(x+cw-30,y+128)],fill=LINE,width=2)
        ctr(d,x+cw/2,y+146,"the tell",SMB,MUTED)
        yy=y+182
        for ln in q.split("\n"):
            ctr(d,x+cw/2,yy,ln,SM,INK); yy+=26
        ctr(d,x+cw/2,y+ch-44,nm,TINYB,col)
    ctr(d,W/2,y+ch+30,"Greenfield or 10-year-old monolith, the questions are the same.",SM,MUTED)
    im.save(os.path.join(OUT,"solid-cover.png"))


# 2. DETECTION TABLE
def table():
    W,H=1280,820; im,d=cv(W,H)
    ctr(d,W/2,30,"The SOLID smell test: grep for the symptom, then ask one question",H3,INK)
    cols=[("","",70),("Grep / smell you can see",None,430),("The one question to ask",None,360),("The refactor move",None,330)]
    x0=40; xs=[x0]
    for _,_,w in cols: xs.append(xs[-1]+w)
    top=78; hh=46
    # header
    rr(d,[x0,top,xs[-1],top+hh],10,fill=INK)
    heads=["","Grep / smell you can see","The one question to ask","The refactor move"]
    for i,h in enumerate(heads):
        if h: d.text((xs[i]+16,top+11),h,font=SMB,fill=CARD)
    rows=[("S",RED,RED_BG,
           "a function you can only describe using \"and\"; a file doing IO + logic + formatting",
           "Does it change for more than one reason?",
           "split by reason into cohesive units"),
          ("O",AMBER,AMBER_BG,
           "long if/elif on a type or role; nested ternaries; error mapping by string keyword",
           "To add a case, do I edit old code or add new?",
           "lookup table, polymorphism, or a variant prop"),
          ("L",BLUE,BLUE_BG,
           "an override that raises NotImplemented; isinstance branches; a subtype that narrows output",
           "Can I swap implementations and keep every guarantee?",
           "honor the base contract, or don't subtype"),
          ("I",TEAL,(214,238,235),
           "props full of ignored optionals; importing a huge module for one helper; bundled permissions",
           "Does the caller depend on things it never uses?",
           "slice the fat interface into small ones"),
          ("D",GREEN,GREEN_BG,
           "ORM .objects.* inside a view; fetch() inside a component; concrete imports in business logic",
           "Does high-level code name a low-level detail?",
           "depend on an abstraction: repository / port")]
    y=top+hh
    rh=140
    for L,col,bg,smell,q,move in rows:
        rr(d,[x0,y,xs[-1],y+rh],0,fill=bg)
        d.rectangle([x0,y,xs[1],y+rh],fill=col)
        ctr(d,(x0+xs[1])/2,y+rh/2-26,L,font("segoeuib.ttf",46),fill=CARD)
        para(d,xs[1]+16,y+18,smell,SM,cols[1][2]-30,fill=INK,lh=26)
        para(d,xs[2]+16,y+18,q,BB,cols[2][2]-30,fill=INK,lh=30)
        para(d,xs[3]+16,y+18,move,SM,cols[3][2]-30,fill=col,lh=26)
        y+=rh
        d.line([(x0,y),(xs[-1],y)],fill=CARD,width=2)
    d.rectangle([x0,top,xs[-1],y],outline=LINE,width=2)
    im.save(os.path.join(OUT,"solid-detection-table.png"))


# 3. DIP before/after (real example from the backend)
def dip():
    W,H=1280,540; im,d=cv(W,H)
    ctr(d,W/2,28,"DIP, detected: an ORM call hiding inside an HTTP view",H3,INK)
    colw=560; lx=60; rx=W-60-colw; top=92; bh=380
    # before
    rr(d,[lx,top,lx+colw,top+bh],14,fill=CARD,outline=RED,width=3)
    chip(d,lx+20,top+18,"SMELL",SMB,CARD,RED); d.text((lx+colw-150,top+20),"tight coupling",font=SMB,fill=RED)
    bx=lx+24
    def box(d,x,y,w,h,label,col,sub=""):
        rr(d,[x,y,x+w,y+h],10,fill=PAPER,outline=col,width=2); ctr(d,x+w/2,y+h/2-(20 if sub else 12),label,BB,INK)
        if sub: ctr(d,x+w/2,y+h/2+10,sub,TINY,MUTED)
    box(d,bx,top+70,colw-48,64,"AdminPengajuanView",RED,"the HTTP layer")
    arrow(d,lx+colw/2,top+138,lx+colw/2,top+178,color=RED,width=3)
    rr(d,[bx,top+182,bx+colw-48,top+250],10,fill=RED_BG,outline=RED,width=2)
    d.text((bx+16,top+196),"Pengajuan.objects.all().order_by(...)",font=MONO,fill=RED)
    d.text((bx+16,top+222),"KaprodiModel.objects.filter(...)",font=MONO,fill=RED)
    para(d,bx,top+266,"The view knows the database. It now changes for HTTP reasons and for persistence reasons.",SM,colw-48,fill=MUTED,lh=24)
    # after
    rr(d,[rx,top,rx+colw,top+bh],14,fill=CARD,outline=GREEN,width=3)
    chip(d,rx+20,top+18,"CLEAN",SMB,CARD,GREEN); d.text((rx+colw-130,top+20),"inverted",font=SMB,fill=GREEN)
    ax=rx+24
    box(d,ax,top+70,colw-48,58,"AdminPengajuanView",GREEN,"thin orchestrator")
    arrow(d,rx+colw/2,top+128,rx+colw/2,top+162,color=GREEN,width=3)
    box(d,ax,top+166,colw-48,58,"PengajuanRepository",BLUE,"the abstraction / port")
    arrow(d,rx+colw/2,top+224,rx+colw/2,top+258,color=GREEN,width=3)
    box(d,ax,top+262,colw-48,52,"Django ORM",MUTED,"")
    para(d,ax,top+326,"Methods named by intent: list_all_ordered(), get_by_pk(). The view depends on intent, not on tables.",SM,colw-48,fill=MUTED,lh=24)
    im.save(os.path.join(OUT,"solid-dip-before-after.png"))


# 4. Over-extraction curve (AI limitation)
def overext():
    W,H=1280,470; im,d=cv(W,H)
    ctr(d,W/2,28,"The over-extraction trap: more files is not more SOLID",H3,INK)
    states=[("1 god module","everything in one place","too coupled",RED,1),
            ("a few cohesive units","each with one clear reason","the sweet spot",GREEN,4),
            ("23 micro-files","logic scattered to the wind","cognitively overwhelming",RED,9)]
    cw=360; gap=40; x0=(W-3*cw-2*gap)//2; y=92; ch=300
    for i,(t,sub,verdict,col,frags) in enumerate(states):
        x=x0+i*(cw+gap); rr(d,[x,y,x+cw,y+ch],14,fill=CARD,outline=col,width=3 if i==1 else 2)
        ctr(d,x+cw/2,y+20,t,BB,INK); ctr(d,x+cw/2,y+52,sub,SM,MUTED)
        # draw fragments
        gy=y+95; gh=120; per=5; bxw=46; bxh=26; pad=10
        cols_=min(per,frags); rows_=(frags+per-1)//per
        gw=cols_*bxw+(cols_-1)*pad; gx=x+(cw-gw)//2
        n=0
        for rr_ in range(rows_):
            for cc in range(per):
                if n>=frags: break
                fx=gx+cc*(bxw+pad); fy=gy+rr_*(bxh+pad)
                rr(d,[fx,fy,fx+bxw,fy+bxh],5,fill=(col[0],col[1],col[2]) if i==1 else PAPER,outline=col,width=2)
                n+=1
        chip(d,x+(cw-d.textlength(verdict,font=SMB)-24)//2,y+ch-46,verdict,SMB,CARD if i==1 else col,col if i==1 else (PAPER))
    ctr(d,W/2,y+ch+20,"AI loves to split. Ask it to stop at the sweet spot, where each unit still earns its name.",SM,MUTED)
    im.save(os.path.join(OUT,"solid-overextraction.png"))


# 5. AI helps vs blind spots
def ai():
    W,H=1280,560; im,d=cv(W,H)
    ctr(d,W/2,28,"Pair-refactoring with AI: real leverage, real blind spots",H3,INK)
    colw=560; lx=60; rx=W-60-colw; top=92; bh=410
    helps=["Spots smells fast: greps for .objects in views, nested ternaries, fat props across the whole repo",
           "Drafts the mechanical extraction and the tests that prove behaviour did not move",
           "Names things well: PengajuanRepository, LaporanNotFoundError, list_all_ordered()",
           "Explains the pattern and cites where it comes from"]
    hurts=["Over-extracts: turns one clear class into twelve, and the team drowns in indirection",
           "Abstracts before there are two real use cases, shipping YAGNI interfaces",
           "Shallow on LSP and contracts: it cannot feel which guarantee actually matters",
           "Pattern-matches without the cost: it never pays the maintenance bill you will"]
    rr(d,[lx,top,lx+colw,top+bh],14,fill=CARD,outline=GREEN,width=3)
    chip(d,lx+20,top+18,"WHERE AI HELPS",SMB,CARD,GREEN)
    y=top+62
    for h in helps:
        d.text((lx+22,y),"+",font=BB,fill=GREEN); y=para(d,lx+48,y,h,SM,colw-70,fill=INK,lh=24)+14
    rr(d,[rx,top,rx+colw,top+bh],14,fill=CARD,outline=RED,width=3)
    chip(d,rx+20,top+18,"WHERE AI MISLEADS",SMB,CARD,RED)
    y=top+62
    for h in hurts:
        d.text((rx+22,y),"!",font=BB,fill=RED); y=para(d,rx+48,y,h,SM,colw-70,fill=INK,lh=24)+14
    im.save(os.path.join(OUT,"solid-ai-helps-hurts.png"))


cover(); table(); dip(); overext(); ai()
print("done")
for f in ["solid-cover.png","solid-detection-table.png","solid-dip-before-after.png","solid-overextraction.png","solid-ai-helps-hurts.png"]:
    print(f, os.path.getsize(os.path.join(OUT,f)),"bytes")
