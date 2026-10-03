#pragma once
#include <string>

namespace scottland::goo
{
// Port of docs/prototypes/goo-lab.html. Textures hold the source list so window count does not
// consume fragment uniform vectors. The GLES 2 variant also works with packed RGBA8 targets.
inline const std::string vertex = R"(
attribute vec2 position;
varying vec2 pos;
uniform mat4 MVP;
void main() { pos = position; gl_Position = MVP * vec4(position, 0, 1); }
)";
inline const std::string common = R"(
precision highp float;
varying vec2 pos;
uniform sampler2D uSources, uFalloff;
uniform int uCount;
uniform vec2 uRes, uSize;
uniform float uTime, uReach, uNoise, uNoiseScale, uNoiseSpeed, uT, uPacked, uThickness;
uniform float uOverlap,uFilm,uCloudiness,uEmissivity,uControls;
vec4 source(int i, float column) { return texture2D(uSources, vec2((column+.5)/8., (float(i)+.5)/float(max(uCount,1)))); }
float hash(vec2 p) { p = fract(p * vec2(123.34,456.21)); p += dot(p,p+45.32); return fract(p.x*p.y); }
float vnoise(vec2 p) {
  vec2 i=floor(p), f=fract(p), u=f*f*(3.-2.*f);
  return mix(mix(hash(i),hash(i+vec2(1,0)),u.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),u.x),u.y);
}
float fbm(vec2 p) { float s=0.,a=.5; for(int k=0;k<3;k++){s+=a*vnoise(p);p=p*2.03+17.1;a*=.5;} return s/.875; }
float sdBox(vec2 p,vec2 b,float r){r=min(r,min(b.x,b.y));vec2 q=abs(p)-b+r;return length(max(q,0.))+min(max(q.x,q.y),0.)-r;}
float unionSdf(vec2 p) {
  float d=1e9;
  for(int i=0;i<1024;i++){if(i>=uCount)break;vec4 r=source(i,0.);d=min(d,sdBox(p-r.xy,r.zw,source(i,1.).y));} return d;
}
float fall(float e) {
  float t=max(e,0.)/(4.*uReach);
  float index=min(t,1.)*255.,lo=floor(index);
  float a=texture2D(uFalloff,vec2((lo+.5)/256.,.5)).r,b=texture2D(uFalloff,vec2((min(lo+1.,255.)+.5)/256.,.5)).r;
  return mix(a,b,index-lo)*exp(-max(0.,e/uReach-4.));
}
// Sources are front to back. Only sources preceding the first content at p
// may put goo over that content. With no overlap retain the original fast path.
vec2 backdrop(vec2 p) {
  if(uOverlap<.5)return vec2(float(uCount),0.);
  for(int i=0;i<1024;i++){
    if(i>=uCount)break;vec4 r=source(i,0.);
    float d=sdBox(p-r.xy,r.zw,source(i,1.).y);
    if(d<=0.)return vec2(float(i),d);
  }return vec2(float(uCount),0.);
}
float surfaceSdf(vec2 p){
  float d=1e9;
  for(int i=0;i<1024;i++){
    if(i>=uCount)break;vec4 r=source(i,0.);float e=sdBox(p-r.xy,r.zw,source(i,1.).y);
    if(e<=0.)return i==0 ? e : d;
    d=min(d,e);
  }return d;
}
float edgeDistance(vec2 p,vec4 r,vec4 g,vec2 back,int i){
  float e=max(sdBox(p-r.xy,r.zw,g.y),0.);
  if(back.x<float(uCount)){
    // Film starts at uFilm and swells by the same ratio as this source's outer goo.
    // It still opens into the full liquid at the back window's shore.
    float width=mix(source(i,5.).z,uThickness,1.-smoothstep(0.,uReach,-back.y));
    e*=uThickness/max(width,.01);
  }return e;
}
float controlCloud(vec2 p,int i){
  if(uControls<.5)return 0.;
  vec4 r=source(i,0.),corners=source(i,3.),sides=source(i,6.);
  float extent=source(i,5.).y;
  vec2 q=p-r.xy;
  vec2 corner=smoothstep(r.zw-vec2(extent),r.zw-vec2(extent)+6.,abs(q));
  float c=q.y<0.?(q.x<0.?corners.x:corners.y):(q.x<0.?corners.z:corners.w);
  float side=abs(q.x)-r.z>abs(q.y)-r.w?(q.x>0.?sides.y:sides.w):(q.y>0.?sides.z:sides.x);
  return mix(side,c,corner.x*corner.y);
}
float deposit(vec2 p,vec4 r,vec4 corners,vec4 dot) {
  float a=0.;
  for(int k=0;k<4;k++){
    vec2 c=r.xy+vec2(k==1||k==3?r.z:-r.z,k>=2?r.w:-r.w);
    float d=length(p-c)/30.; a+=corners[k]*.22*exp(-d*d);
  }
  float d=length(p-dot.xy)/12.; return a+dot.z*.45*exp(-d*d);
}
vec3 gooField(vec2 p) {
  float F=0.,cloud=0.,weight=0.,breathing=0.;vec2 back=backdrop(p);
  // Extend the front source under its own content for bilinear reconstruction.
  // Rendering and the flow mask still clip that content analytically.
  if(back.x==0.)back=vec2(1.,0.);
  for(int i=0;i<1024;i++){
    if(i>=int(back.x))break;vec4 r=source(i,0.),g=source(i,1.);
    // A hint's own halo remains visible over app content even when window film is off.
    if(g.x<=0.||(back.y<0.&&uFilm<=0.&&source(i,5.).w<.5))continue;
    float e=edgeDistance(p,r,g,back,i),fe=fall(e);
    if(fe==0.)continue;
    float n=fbm(p*uNoiseScale+g.z*vec2(7.13,3.71)+vec2(uTime*uNoiseSpeed,-uTime*uNoiseSpeed*.73));
    float scale=clamp(abs(source(i,2.).w),0.,1.);
    float control=controlCloud(p,i);
    if(uControls>.5){cloud+=g.x*fe*control;weight+=g.x*fe;}
    float a=max(g.x*(1.+uNoise*scale*(n-.5)*2.),uT/max(fall(max(uThickness*.1*scale,source(i,5.).x)),.0001))
      +.22*uCloudiness*control+deposit(p,r,vec4(0.),source(i,4.));
    if(back.x<float(uCount)&&back.y<0.)a/=max(g.x,.0001);
    float contribution=max(a,0.)*fe;
    F+=contribution;
    // Finite support applies only to the decorative modulation, never field/dye tails.
    float local=1.-smoothstep(3.*uReach,4.*uReach,max(sdBox(p-r.xy,r.zw,g.y),0.));
    breathing+=contribution*source(i,7.).x*local;
  } return vec3(F,cloud/max(weight,.0001),breathing/max(F,.0001));
}
vec2 off(int k){return k==0?vec2(1,0):k==1?vec2(-1,0):k==2?vec2(0,1):vec2(0,-1);}
vec2 decode(vec4 hv){
  // Two bytes per signed component retain small velocities that carry a wave
  // along a thin border. One byte rounded them to zero before they propagated.
  vec2 q=vec2(hv.r+hv.g*256.,hv.b+hv.a*256.)*255.;
  return mix(hv.rg,(q-32768.)*8./65535.,uPacked);
}
vec4 encode(vec2 hv){
  // Round toward zero so residual waves settle, with 16-bit precision in RGBA8.
  vec2 q=sign(hv)*floor(abs(hv)*65535./8.+.00001)+32768.;
  vec4 bytes=vec4(mod(q.x,256.),floor(q.x/256.),mod(q.y,256.),floor(q.y/256.))/255.;
  return mix(vec4(hv,0,1),bytes,uPacked);
}
)";
inline const std::string mask = R"(
uniform sampler2D uField;
float field(vec2 uv){
  float f=texture2D(uField,uv).r;
  return uPacked>.5 ? exp(f*2.83321334)-1. : f;
}
float gooMask(vec2 uv){return smoothstep(uT*.97,uT*1.03,field(uv))*step(0.,uOverlap>.5?surfaceSdf(uv*uRes):unionSdf(uv*uRes));}
)";
inline const std::string field_shader = common + R"(
void main(){
  vec2 p=gl_FragCoord.xy*uRes/uSize;
  float d=uOverlap>.5?surfaceSdf(p):unionSdf(p);
  // Deep inside a window the goo is hidden and never read: any value over the threshold will do.
  vec3 value=d<-8.?vec3(uT*4.,0.,0.):gooField(p);
  float f=value.x,cloud=value.y;
  // Log packing spends RGBA8 precision at the boundary, avoiding staircase edges.
  if(uPacked>.5)f=log(1.+f)/2.83321334;
  gl_FragColor=vec4(f,step(0.,d),cloud,value.z);
}
)";
// Mask at quarter-resolution texel centers for the two wave stencils. Dye
// retains its original SDF evaluations (including continuous advection positions).
inline const std::string mask_shader = common + mask + R"(
void main(){
  vec2 uv=gl_FragCoord.xy/uSize;
  float f=smoothstep(uT*.97,uT*1.03,field(uv));
  gl_FragColor=vec4(f>0.?step(0.,uOverlap>.5?surfaceSdf(uv*uRes):unionSdf(uv*uRes)):0.,0,0,1);
}
)";
inline const std::string cached_mask = R"(
uniform sampler2D uMask;
float gridMask(vec2 uv){
  if(any(lessThan(uv,vec2(0.)))||any(greaterThan(uv,vec2(1.))))return gooMask(uv);
  return smoothstep(uT*.97,uT*1.03,field(uv))*texture2D(uMask,uv).r;
}
)";
inline const std::string wave_shader = common + mask + cached_mask + R"(
uniform sampler2D uWave;
uniform float uC2,uDamp;
uniform vec4 uImp[8];
uniform int uImpN;
void main(){
  vec2 uv=gl_FragCoord.xy/uSize,px=1./uSize,p=uv*uRes;
  vec2 hv=decode(texture2D(uWave,uv));
  float m=gridMask(uv),hc=hv.x,sum=0.;
  for(int k=0;k<4;k++){vec2 u2=uv+off(k)*px;sum+=mix(hc,decode(texture2D(uWave,u2)).x,gridMask(u2));}
  float v=(hv.y+uC2*(sum-4.*hc))*uDamp,h=hc+v;
  h*=mix(.8,1.,m);v*=mix(.8,1.,m);
  // Every closed goo band has a constant-height mode: velocity damping alone
  // cannot remove displacement left by a positive impulse. Damp both on every path.
  h*=uDamp;
  for(int k=0;k<8;k++){if(k>=uImpN)break;float dd=distance(p,uImp[k].xy);h+=uImp[k].z*exp(-dd*dd/(uImp[k].w*uImp[k].w))*m;}
  gl_FragColor=encode(clamp(vec2(h,v),-3.9,3.9));
}
)";
inline const std::string dye_shader = common + mask + R"(
uniform sampler2D uDyeTex;
uniform float uSpread,uSwirl,uRelease,uSoak;
uniform sampler2D uWallpaper;
uniform mat4 uWallpaperMap;
void main(){
  vec2 uv=gl_FragCoord.xy/uSize,px=1./uSize,p=uv*uRes;
  float m=gooMask(uv);
  vec3 c=texture2D(uDyeTex,uv).rgb;
  if(m>0.){ // the swirl only moves dye that is in goo
    vec2 q=p*.004+vec2(0.,uTime*.03);float h=.01;
    float gx=(fbm(q+vec2(h,0))-fbm(q-vec2(h,0)))/(2.*h),gy=(fbm(q+vec2(0,h))-fbm(q-vec2(0,h)))/(2.*h);
    vec2 vel=vec2(gy,-gx)*uSwirl,adv=uv-vel/uRes;
    // Both ends must contain goo: backtracing cannot pull color across a dry gap.
    c=texture2D(uDyeTex,mix(uv,adv,gooMask(adv)*m)).rgb;
  }
  if(m>0.){
    vec3 acc=c;float ws=1.;
    for(int k=0;k<4;k++){vec2 u2=uv+off(k)*px;float w=gooMask(u2)*m;acc+=texture2D(uDyeTex,u2).rgb*w;ws+=w;}
    c=mix(c,acc/ws,uSpread);
  }
  float ksum=1e-4,maxK=0.,nearEdge=1e5;vec3 nearest=vec3(0);vec2 back=backdrop(p);
  // Retain the front source's dye under its own island as well: interpolation
  // at a thin film must not mix its color with black dry texels inside content.
  if(back.x==0.)back=vec2(1.,0.);
  for(int i=0;i<1024;i++){
    if(i>=int(back.x))break;vec4 r=source(i,0.),g=source(i,1.);float e=edgeDistance(p,r,g,back,i);
    float k=g.x*fall(e);maxK=max(maxK,k);ksum+=k;nearEdge=min(nearEdge,e);nearest+=k*source(i,2.).rgb;
  }
  for(int i=0;i<1024;i++){
    if(i>=int(back.x))break;vec4 r=source(i,0.),g=source(i,1.);float e=edgeDistance(p,r,g,back,i);
    float k=g.x*fall(e); if(k<maxK-.00001)continue;
    float w=uRelease*g.w*exp(-e/(uReach*.6));
    // At the wall, keep state ink ahead of wallpaper color diffusing inward.
    // The extra anchoring follows the user's release setting and vanishes in
    // the open band; without a wallpaper source uSoak is zero.
    if(uSoak>0.)w*=1.+3.*uSoak*(1.-smoothstep(0.,uThickness*.7,e));
    vec3 tint=source(i,2.).rgb;
    // State marks are released dye, Gaussian deposits, never overlay geometry.
    float cloud=controlCloud(p,i)*uCloudiness;
    float dd=length(p-source(i,4.).xy)/12.,dot=source(i,4.).z*exp(-dd*dd);
    vec3 mark=source(i,2.).w>0.?vec3(.08,.08,.1):vec3(1.);
    tint=mix(tint,vec3(1.),clamp(cloud*.7,0.,.8));
    tint=mix(tint,mark,clamp(dot,0.,1.));
    c=mix(c,tint,clamp(w,0.,1.));
  }
  // Watercolor pickup: release a weak wallpaper dye into history, where it
  // advects and diffuses. The shore distance vanishes at every window wall;
  // summed source density and bridge contributions favor thick pooled liquid.
  // State release above remains stronger, and hint dye still wins at draw time.
  if(m>0.&&uSoak>0.){
    float pool=clamp((ksum-maxK)/max(maxK,1e-4),0.,1.);
    float wash=sqrt(smoothstep(0.,uThickness*.7,nearEdge))*
      smoothstep(uT*.7,uT*1.4,ksum)*mix(.65,1.3,pool);
    vec2 wallpaperUV=(uWallpaperMap*vec4(p,0,1)).xy*.5+.5;
    c=mix(c,texture2D(uWallpaper,wallpaperUV).rgb,
      uSoak*min(.03,uRelease*.5)*wash*m);
  }
  c=mix(c,nearest/ksum,(1.-m)*.25);
  gl_FragColor=vec4(c,1);
}
)";
inline const std::string render_shader = common + mask + R"(
uniform sampler2D uWave,uDyeTex,uBackground;
uniform float uWaveAmp,uShine,uRelief,uAlpha,uHints,uDepth,uProfile,uSoak;
uniform vec2 uFieldSize;
uniform float uBreath,uBreathSwell;
uniform mat4 uBackgroundMap;
// Positive cubic B-spline weights: four bilinear fetches reconstruct a smooth
// contour without overshoot/ringing. Only the full-resolution draw uses this;
// waves, dye and input retain the original field. Interpolate packed log values
// before decoding, just as the bilinear packed path does.
vec4 drawField(vec2 uv){
  vec2 q=uv*uFieldSize-.5,base=floor(q),f=q-base,g=1.-f;
  vec2 w0=g*g*g/6.,w1=(3.*f*f*f-6.*f*f+4.)/6.;
  vec2 w2=(-3.*f*f*f+3.*f*f+3.*f+1.)/6.,w3=f*f*f/6.;
  vec2 a=w0+w1,b=w2+w3;
  vec2 lo=(base-.5+w1/a)/uFieldSize,hi=(base+1.5+w3/b)/uFieldSize;
  return mix(mix(texture2D(uField,lo),texture2D(uField,vec2(hi.x,lo.y)),b.x),
             mix(texture2D(uField,vec2(lo.x,hi.y)),texture2D(uField,hi),b.x),b.y);
}
float drawDensity(vec4 value){return uPacked>.5?exp(value.r*2.83321334)-1.:value.r;}
float surfaceHeight(float F,float wall,float filmScale){
  // Density gives distance inward from the free surface. Combined with wall
  // distance it parameterizes the entire band, including pooled/bridged liquid.
  float inward=max(log(max(F,1e-4)/uT)*uReach*filmScale,0.);
  float width=max(inward+max(wall,0.),.001);
  float t=clamp(inward/width,0.,1.);
  // Rounded bead, plus a wetting meniscus at the wall. Thin films and scaled
  // borders carry proportionally less depth; broad pools keep their dome.
  float bead=sin(3.14159265*t);
  float wet=t*t*t; wet*=wet;
  return uDepth*min(width/max(uThickness,1.),1.5)*(bead+uProfile*wet);
}
void main(){
  vec2 p=pos,uv=p/uRes;
  vec4 value=drawField(uv);
  float pulse=value.a*uBreath;
  float F=drawDensity(value)*(1.+uBreathSwell*pulse),h=decode(texture2D(uWave,uv)).x,Fe=F*(1.+uWaveAmp*h);
  // Evaluate derivatives before any nonuniform discard. fwidth is in device
  // pixels, independent of output/window scale; film and control outlines share
  // this same iso-surface. Keep the existing analytic window-edge exclusion.
  float aa=max(.5*fwidth(Fe),1e-6);
  float d=uOverlap>.5?surfaceSdf(p):unionSdf(p);
  bool film=uOverlap>.5&&backdrop(p).x<float(uCount);
  float ht=surfaceHeight(Fe,d,film?min(uFilm/uThickness,1.):1.);
  // Derivatives of the reconstructed surface replace two extra cubic samples.
  // Invert the logical-position Jacobian: works at fractional scale and rotation.
  // All derivatives execute before nonuniform discards, including at the shore.
  vec2 dx=dFdx(p),dy=dFdy(p);
  float hx=dFdx(ht),hy=dFdy(ht),det=dx.x*dy.y-dx.y*dy.x;
  vec2 slope=vec2(hx*dy.y-hy*dx.y,hy*dx.x-hx*dy.x)/det;
  slope*=uRelief/5.;
  if(d<=0.)discard;
  float a=smoothstep(uT-aa,uT+aa,Fe)*smoothstep(0.,1.,d)*uAlpha;
  if(a<=0.)discard;
  // WK14: window mode tints the goo with each hinted window's color at once, blended by
  // contribution so connected goo stays smooth; there is no separate rim.
  float hintAmount=0.;vec3 hintDye=vec3(0.);
  vec2 hintBack=backdrop(p);
  if(uHints>.5)for(int i=0;i<1024;i++){
    if(i>=int(hintBack.x))break;if(source(i,5.).x<=0.)continue;
    vec4 r=source(i,0.),g=source(i,1.);float contribution=g.x*fall(max(sdBox(p-r.xy,r.zw,g.y),0.));
    hintDye+=source(i,2.).rgb*contribution;hintAmount+=contribution;
  }
  float cloud=uControls>.5?value.b:0.;
  vec3 n=normalize(vec3(-slope,1.));
  // Keep the shipped one-pixel exclusion around window content.
  vec2 refr=p-clamp(slope,vec2(-2.),vec2(2.))*(film?1.:8.); if(unionSdf(refr)<1.)refr=p;
  vec2 bgUV=(uBackgroundMap*vec4(refr,0,1)).xy*.5+.5;
  vec3 bg=texture2D(uBackground,bgUV).rgb,dye=texture2D(uDyeTex,uv).rgb;
  if(hintAmount>0.)dye=hintDye/hintAmount;
  vec3 L=normalize(vec3(-.45,-.55,.7));float diff=.6+.4*dot(n,L);
  float spec=pow(max(dot(reflect(-L,n),vec3(0,0,1)),0.),48.)*uShine;
  float rim=1.-smoothstep(0.,.5,max(log(max(Fe,1e-4)/uT),0.));
  float swirl=cloud>.001?.75+.25*fbm(p*.045+vec2(uTime*.13,-uTime*.09)):1.;
  float milk=cloud*uCloudiness*swirl;
  dye=mix(dye,vec3(1.),milk*.8);
  // The wet outer band can show refracted paper. At the wall, state dye
  // supplies the color so saturated wallpaper cannot repaint a focused edge.
  float dyeBlend=.55+milk*.25;
  if(uSoak>0.)dyeBlend=mix(dyeBlend,1.,1.-smoothstep(0.,uThickness*.9,d));
  vec3 color=mix(bg*(film?1.:1.4),dye*.85,dyeBlend)*diff+spec*vec3(1.,.98,.95)+dye*rim*.22;
  // Emission is independent of normal, light and dye release. Zero really is off.
  color+=cloud*uEmissivity*mix(dye,vec3(1.),.65);
  color+=.25*pulse*mix(dye,vec3(1.),.25);
  a*=film?mix(.48,.78,milk):mix(.96,1.,milk);
  gl_FragColor=vec4(clamp(color,0.,1.)*a,a);
}
)";
// The settled surface is independent of the scene beneath it. Cache its own
// color/coverage and the background's refraction/lighting coefficient; changing
// windows then need only this short composite rather than all SDF/depth work.
inline const std::string cached_composite_shader = R"(
precision highp float;
varying vec2 pos;
uniform sampler2D uIntrinsic,uRefraction,uBackground;
uniform mat4 uBackgroundMap;
void main(){
  vec2 uv=(uBackgroundMap*vec4(pos,0.,1.)).xy*.5+.5;
  vec4 intrinsic=texture2D(uIntrinsic,uv);
  if(intrinsic.a<=0.)discard;
  vec4 refr=texture2D(uRefraction,uv);
  vec2 shifted=pos+(refr.rg-.5)*32.;
  vec2 bgUV=(uBackgroundMap*vec4(shifted,0.,1.)).xy*.5+.5;
  vec3 bg=texture2D(uBackground,bgUV).rgb;
  vec3 color=clamp(intrinsic.rgb+refr.b*1.5*bg,0.,1.);
  gl_FragColor=vec4(color*intrinsic.a,intrinsic.a);
}
)";
// Max-reduction of changes in dye and wave energy, read back as a single pixel every 30 steps.
inline const std::string energy_shader = common + R"(
uniform sampler2D uWave,uDyeTex,uPrevious,uReduce;
uniform int uFirst;
uniform float uDyeVisible;
uniform vec2 uInputSize;
void main(){
  vec3 e=vec3(0.);vec2 start=(gl_FragCoord.xy-.5)*2.;
  for(int y=0;y<2;y++)for(int x=0;x<2;x++){
    vec2 uv=(start+vec2(float(x),float(y))+.5)/uInputSize;
    vec3 v;
    if(uFirst==1){vec2 hv=decode(texture2D(uWave,uv));vec3 dc=abs(texture2D(uDyeTex,uv).rgb-texture2D(uPrevious,uv).rgb);float wave=max(abs(hv.x),abs(hv.y)),dye=max(max(dc.r,dc.g),dc.b)*16.*uDyeVisible;v=vec3(max(wave,dye),wave,dye);}
    else v=texture2D(uReduce,uv).rgb;
    e=max(e,v);
  }
  gl_FragColor=vec4(e,1);
}
)";
inline const std::string query_shader = common + R"(
uniform sampler2D uWave;
uniform vec2 uPoint;
uniform sampler2D uDyeTex;
void main(){float h=decode(texture2D(uWave,uPoint/uRes)).x;gl_FragColor=vec4(texture2D(uDyeTex,uPoint/uRes).rgb,h/8.+128./255.);}
)";
} // namespace scottland::goo
