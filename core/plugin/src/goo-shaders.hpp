#pragma once
#include <string>
#include <vector>

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
// Sample four encoded texel centers, decode each, then interpolate pigment absorbance.
// Hardware interpolation of sqrt(K/6) would systematically lighten transported boundaries.
inline const std::string dye_filter = R"(
uniform vec2 uDyeSize;
vec3 dyeK(vec4 stored){return 6.*stored.rgb*stored.rgb;}
vec3 sampleDyeK(sampler2D tex,vec2 uv){
  vec2 grid=uv*uDyeSize-.5,lo=floor(grid),f=fract(grid);
  vec2 a=(lo+.5)/uDyeSize,b=(lo+1.5)/uDyeSize;
  vec3 aa=dyeK(texture2D(tex,a)),ba=dyeK(texture2D(tex,vec2(b.x,a.y)));
  vec3 ab=dyeK(texture2D(tex,vec2(a.x,b.y))),bb=dyeK(texture2D(tex,b));
  return mix(mix(aa,ba,f.x),mix(ab,bb,f.x),f.y);
}
)";
inline const std::string common = R"(
precision highp float;
varying vec2 pos;
uniform sampler2D uSources, uFalloff, uShapes;
uniform vec2 uAtlasSize;
uniform int uCount;
uniform vec2 uRes, uSize;
uniform float uTime, uReach, uNoise, uNoiseScale, uNoiseSpeed, uT, uPacked, uThickness;
#ifdef GOO_FAST
// The common resting/breathing path: no overlap film, no control proximity, and every source
// is eligible, so loops end at uCount. Uniform branches alone keep the overlap/hover loop
// state alive on Xe even when both are absent.
uniform float uFilm,uCloudiness,uEmissivity,uDyeStrength; const float uOverlap=0.,uControls=0.;
#define GOO_BOUND(n) uCount
#else
uniform float uOverlap,uFilm,uCloudiness,uEmissivity,uControls,uDyeStrength;
#define GOO_BOUND(n) int(n)
#endif
// GO28: the dye is one field of pigment absorbance (K = -ln color), stored as sqrt(K/6) so
// RGBA8 keeps its precision at the light end. Mixing absorbances is subtractive, like pigment.
// uPickup is the pickup rate relative to release (0 off; soak and pickup balance), uPickupRich
// soak^0.25 (0 off); uOpenPickup is 0 when open desktop has
// no background-layer client (nothing to pick up there).
uniform float uPickup,uOpenPickup,uPickupRich;
)" + dye_filter + R"(
vec4 storeK(vec3 k){return vec4(sqrt(clamp(k/6.,0.,1.)),1.);}
vec3 absorb(vec3 c){return -log(clamp(c,.0025,1.));}
vec3 dyeColor(vec4 stored){return exp(-dyeK(stored));}
// How much pigment a source's release carries: A16 neutral strength for its neutral share, all of
// it for its state share (0 is clear water). Dye density scales all of the goo's dye together,
// picked-up color included (Mike, 2026-10-05), so it enters the amount below, not the mix.
float sourceAmount(vec4 c7){return c7.w*(1.-c7.z)+c7.z;}
vec4 source(int i, float column) { return texture2D(uSources, vec2((column+.5)/11., (float(i)+.5)/max(float(uCount),1.))); }
float hash(vec2 p) { p = fract(p * vec2(123.34,456.21)); p += dot(p,p+45.32); return fract(p.x*p.y); }
float vnoise(vec2 p) {
  vec2 i=floor(p), f=fract(p), u=f*f*(3.-2.*f);
  return mix(mix(hash(i),hash(i+vec2(1,0)),u.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),u.x),u.y);
}
float fbm(vec2 p) { float s=0.,a=.5; for(int k=0;k<3;k++){s+=a*vnoise(p);p=p*2.03+17.1;a*=.5;} return s/.875; }
float sdBox(vec2 p,vec2 b,float r){r=min(r,min(b.x,b.y));vec2 q=abs(p)-b+r;return length(max(q,0.))+min(max(q.x,q.y),0.)-r;}
float sourceSdf(vec2 p,int i) {
  vec4 r=source(i,0.);float radius=source(i,1.).y;
  // Ordinary windows never fetch the widget atlas or its tile coordinates.
  if(radius>=0.)return sdBox(p-r.xy,r.zw,radius);
  vec4 tile=source(i,8.);
  vec4 bounds=source(i,9.),body=source(i,10.);
  vec2 center=(bounds.xy+bounds.zw)*.5,halfSize=max((bounds.zw-bounds.xy)*.5,vec2(.01));
  vec2 stepSize=body.zw/halfSize;
  vec2 at=(p-body.xy)/stepSize+center;
  vec2 q=clamp(at,vec2(.5),tile.zw-.5);
  vec4 c=texture2D(uShapes,(tile.xy+q)/uAtlasSize);
  float d=(dot(c.rg,vec2(255.,65280.))-32768.)/16.+length(at-q);
  return sdBox(p-body.xy,body.zw,0.)+(d-sdBox(at-center,halfSize,0.))*min(stepSize.x,stepSize.y);
}
float unionSdf(vec2 p) {
  float d=1e9;
  for(int i=0;i<1024;i++){if(i>=uCount)break;vec4 r=source(i,0.);d=min(d,sourceSdf(p,i));} return d;
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
    float d=sourceSdf(p,i);
    if(d<=0.)return vec2(float(i),d);
  }return vec2(float(uCount),0.);
}
float surfaceSdf(vec2 p){
  float d=1e9;
  for(int i=0;i<1024;i++){
    if(i>=uCount)break;vec4 r=source(i,0.);float e=sourceSdf(p,i);
    if(e<=0.)return i==0 ? e : d;
    d=min(d,e);
  }return d;
}
float edgeDistance(vec2 p,vec4 r,vec4 g,vec2 back,int i){
  float e=max(sourceSdf(p,i),0.);
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
vec4 gooField(vec2 p) {
  float F=0.,tinted=0.,cloud=0.,weight=0.,breathing=0.;vec2 back=backdrop(p);
  // Extend the front source under its own content for bilinear reconstruction.
  // Rendering and the flow mask still clip that content analytically.
#ifndef GOO_FAST
  if(back.x==0.)back=vec2(1.,0.);
#endif
  for(int i=0;i<1024;i++){
    if(i>=GOO_BOUND(back.x))break;vec4 r=source(i,0.),g=source(i,1.);
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
    // Preserve A16's original field channel exactly at GO23's default. Other
    // values carry the state share separately so neutral dye keeps its own strength.
    tinted+=contribution*sourceAmount(source(i,7.));
    // Finite support applies only to the decorative modulation, never field/dye tails.
    float shore=g.y<0.?sourceSdf(p,i):sdBox(p-r.xy,r.zw,g.y);
    float local=1.-smoothstep(3.*uReach,4.*uReach,max(shore,0.));
    breathing+=contribution*source(i,7.).x*local;
  }
  // GO28: the pigment amount here is the equilibrium of release and pickup, scaled by dye
  // density (up to 1.5). Stored /1.5 so the packed field keeps it.
  float pick=uPickup*(back.x<float(uCount)?1.:uOpenPickup);
  float amount=uDyeStrength*(tinted/max(F,.0001)+pick)/(1.+pick);
  return vec4(F,amount/1.5,cloud/max(weight,.0001),breathing/max(F,.0001));
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
  vec4 value=d<-8.?vec4(uT*4.,0.,0.,0.):gooField(p);
  float f=value.r,cloud=value.b;
  // Log packing spends RGBA8 precision at the boundary, avoiding staircase edges.
  if(uPacked>.5)f=log(1.+f)/2.83321334;
  gl_FragColor=vec4(f,value.g,cloud,value.a);
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
uniform sampler2D uDyeTex,uUnder;
uniform float uSpread,uSwirl,uRelease;
// GO24: uFlow is the swirl's own clock (it runs while the simulation sleeps); uStep is
// how many ordinary steps this pass stands for (1 awake, more for a sleeping tick), and
// uWetOnly leaves dry texels as they are on those ticks.
uniform float uFlow,uStep,uWetOnly;
float often(float rate){return 1.-pow(1.-clamp(rate,0.,.999),uStep);}
void main(){
  vec2 uv=gl_FragCoord.xy/uSize,px=1./uSize,p=uv*uRes;
  float m=gooMask(uv);
  vec4 stored=texture2D(uDyeTex,uv);
  if(m<=0.&&uWetOnly>.5){gl_FragColor=stored;return;}
  vec3 c=dyeK(stored);
  if(m>0.){ // the swirl only moves dye that is in goo
    vec2 q=p*.004+vec2(0.,uFlow*.03);float h=.01;
    float gx=(fbm(q+vec2(h,0))-fbm(q-vec2(h,0)))/(2.*h),gy=(fbm(q+vec2(0,h))-fbm(q-vec2(0,h)))/(2.*h);
    vec2 vel=vec2(gy,-gx)*uSwirl;
    // Watercolor runs along the liquid: most of the flow follows the band (across the
    // field's gradient), so pigment travels down an edge instead of stalling at its sides.
    if(uPickupRich>0.){
      vec2 fp=1./uRes*4.;
      vec2 grad=vec2(field(uv+vec2(fp.x,0.))-field(uv-vec2(fp.x,0.)),field(uv+vec2(0.,fp.y))-field(uv-vec2(0.,fp.y)));
      float gl=length(grad);
      if(gl>1e-5){vec2 n=grad/gl,along=vec2(-n.y,n.x);
        // A slow current along the band, eddying with the noise, plus a little cross flow.
        // It turns over every several seconds, so streaks of pigment lengthen, slacken
        // and reverse. Short currents keep each part of the goo near the colors beneath
        // and beside it (Mike, 2026-10-03: local, never a screen-wide wash).
        float current=(fbm(q*1.2+vec2(11.3,uFlow*.05))-.5)*20.;
        vel=mix(vel,along*(dot(vel,along)+current*sqrt(uSwirl)),.85);}
    }
    vec2 adv=uv-vel*uStep/uRes;
    // Both ends must contain goo: backtracing cannot pull color across a dry gap.
    c=sampleDyeK(uDyeTex,mix(uv,adv,gooMask(adv)*m));
  }
  if(m>0.){
    vec3 acc=c;float ws=1.;
    for(int k=0;k<4;k++){vec2 u2=uv+off(k)*px;float w=gooMask(u2)*m;acc+=sampleDyeK(uDyeTex,u2)*w;ws+=w;}
    // Spread scales with the pass like flow, pickup and release: a coasting pass that stands
    // for almost nothing must not still mix each texel most of the way to its neighbours.
    c=mix(c,acc/ws,clamp(uSpread*uStep,0.,1.));
  }
  float ksum=1e-4,maxK=0.,nearEdge=1e5;vec2 back=backdrop(p);
  // Film over a window picks up that window; open desktop only with a background client.
  float where=back.x<float(uCount)?1.:uOpenPickup;
  // Retain the front source's dye under its own island as well: interpolation
  // at a thin film must not mix its color with black dry texels inside content.
#ifndef GOO_FAST
  if(back.x==0.)back=vec2(1.,0.);
#endif
  for(int i=0;i<1024;i++){
    if(i>=GOO_BOUND(back.x))break;vec4 r=source(i,0.),g=source(i,1.);float e=edgeDistance(p,r,g,back,i);
    float k=g.x*fall(e);maxK=max(maxK,k);ksum+=k;nearEdge=min(nearEdge,e);
  }
  // GO28: one dye. Every source releases into all of its own goo, by its share of the
  // liquid here, a little more at the wall (a new state blooms outward); the color beneath
  // is picked up into the same field. Each texel moves toward the subtractive mixture of
  // everything feeding it; nothing replaces what is there.
  vec3 load=vec3(0.);float rate=0.;
  for(int i=0;i<1024;i++){
    if(i>=GOO_BOUND(back.x))break;vec4 r=source(i,0.),g=source(i,1.);float e=edgeDistance(p,r,g,back,i);
    float k=g.x*fall(e);
    float w=uRelease*g.w*(k/ksum)*mix(.5,1.,exp(-e/(uReach*.6)))*sourceAmount(source(i,7.));
    if(w<=0.)continue;
    vec3 tint=source(i,2.).rgb;
    // State marks are released dye, Gaussian deposits, never overlay geometry.
    float cloud=controlCloud(p,i)*uCloudiness;
    float dd=length(p-source(i,4.).xy)/12.,dot=source(i,4.).z*exp(-dd*dd);
    vec3 mark=source(i,2.).w>0.?vec3(.08,.08,.1):vec3(1.);
    tint=mix(tint,vec3(1.),clamp(cloud*.7,0.,.8));
    tint=mix(tint,mark,clamp(dot,0.,1.));
    load+=w*absorb(tint);rate+=w;
  }
  vec4 under=texture2D(uUnder,uv);
  if(uPickup>0.&&under.a>.5){
    // Graded by thickness, never zero: thin goo takes about two thirds of what thick or
    // pooled liquid does (Mike, 2026-10-03).
    float pool=clamp((ksum-maxK)/max(maxK,1e-4),0.,1.);
    float thick=max(pool,smoothstep(3.,uThickness,nearEdge));
    float pick=uRelease*uPickup*where*mix(.65,1.,thick)*smoothstep(uT*.5,uT,ksum);
    // Wet pigment is a little richer than the paper it lifted from. (Subtractive mixing keeps
    // saturation; a stronger boost turns complementary colors to mud where they meet.)
    vec3 paper=under.rgb;
    paper=clamp(mix(vec3(dot(paper,vec3(.299,.587,.114))),paper,1.+.35*uPickupRich),0.,1.);
    load+=pick*absorb(paper);rate+=pick;
  }
  if(rate>0.)c=mix(c,load/rate,clamp(often(rate)*m+(1.-m)*.25,0.,1.));
  gl_FragColor=storeK(c);
}
)";
// GO28: what lies beneath the goo, at the dye grid's resolution. Drawn only over texels whose
// whole footprint was copied into the backdrop this frame; alpha marks a texel as known.
inline const std::string under_shader = R"(
precision highp float;
uniform sampler2D uBackground;
uniform mat4 uBackgroundMap;
uniform vec2 uRes,uSize;
vec3 at(vec2 p){return texture2D(uBackground,(uBackgroundMap*vec4(p,0,1)).xy*.5+.5).rgb;}
void main(){
  vec2 p=gl_FragCoord.xy*uRes/uSize,h=.25*uRes/uSize;
  gl_FragColor=vec4((at(p+vec2(-h.x,-h.y))+at(p+vec2(h.x,-h.y))+at(p+vec2(-h.x,h.y))+at(p+h))*.25,1.);
}
)";
// GO28: liquid texels whose backdrop differs from the copy the dye last saw (more than 4
// levels in a channel), summed in 1/255 steps over 2x2 blocks; later levels sum their inputs.
// A block past an odd edge skips its missing taps: clamped to the edge, they would count that
// texel or partial sum again.
inline const std::string change_shader = R"(
precision highp float;
uniform sampler2D uUnder,uSeen,uMask,uReduce;
uniform int uFirst;
uniform vec2 uInputSize;
void main(){
  float n=0.;vec2 start=(gl_FragCoord.xy-.5)*2.;
  for(int y=0;y<2;y++)for(int x=0;x<2;x++){
    vec2 t=start+vec2(float(x),float(y));
    if(t.x>=uInputSize.x||t.y>=uInputSize.y)continue;
    vec2 uv=(t+.5)/uInputSize;
    if(uFirst==1){
      vec4 a=texture2D(uUnder,uv),b=texture2D(uSeen,uv);vec3 d=abs(a.rgb-b.rgb);
      n+=texture2D(uMask,uv).r>0.&&a.a>.5&&max(max(d.r,d.g),d.b)>4.5/255.?1./255.:0.;
    }else n+=texture2D(uReduce,uv).r;
  }
  gl_FragColor=vec4(min(n,1.),0,0,1);
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
    if(i>=GOO_BOUND(hintBack.x))break;if(source(i,5.).x<=0.)continue;
    vec4 r=source(i,0.),g=source(i,1.);float contribution=g.x*fall(max(sourceSdf(p,i),0.));
    hintDye+=source(i,2.).rgb*contribution;hintAmount+=contribution;
  }
  // GO28: how much pigment is here (release and pickup), up to 1.5 at maximum dye density.
  float amount=clamp(value.g,0.,1.)*1.5;
  float cloud=uControls>.5?value.b:0.;
  vec3 n=normalize(vec3(-slope,1.));
  // Keep the shipped one-pixel exclusion around window content.
  vec2 refr=p-clamp(slope,vec2(-2.),vec2(2.))*(film?1.:8.); if(unionSdf(refr)<1.)refr=p;
  vec2 bgUV=(uBackgroundMap*vec4(refr,0,1)).xy*.5+.5;
#ifdef GOO_CACHE
  // GO24: neither cache holds the backdrop or the dye. The intrinsic cache is the surface with
  // no dye (hint dye and control milk, which replace or whiten it, stay); the parameter cache
  // carries the dye's share of the color (below). The composite multiplies the live dye in.
  vec3 bg=vec3(0.),dye=vec3(0.);
#else
  vec3 bg=texture2D(uBackground,bgUV).rgb,dye=exp(-sampleDyeK(uDyeTex,uv));
#endif
  if(hintAmount>0.)dye=hintDye/hintAmount;
  vec3 L=normalize(vec3(-.45,-.55,.7));float diff=.6+.4*dot(n,L);
  float spec=pow(max(dot(reflect(-L,n),vec3(0,0,1)),0.),48.)*uShine;
  float rim=1.-smoothstep(0.,.5,max(log(max(Fe,1e-4)/uT),0.));
  float swirl=cloud>.001?.75+.25*fbm(p*.045+vec2(uTime*.13,-uTime*.09)):1.;
  float milk=cloud*uCloudiness*swirl;
  dye=mix(dye,vec3(1.),milk*.8);
  // The liquid is part dye body, part clear lens over what is beneath. GO24's pickup term
  // stays; GO28 scales the body by the pigment that is here (0: clear water).
  float dyeBlend=.55+milk*.25;
  if(uSoak>0.)dyeBlend+=.22*pow(uSoak,.25);
  // Hints (WK14): the body scales with dye density alone, not with how much pigment is here.
  if(hintAmount<=0.)dyeBlend=clamp(dyeBlend*amount,0.,1.);
  else dyeBlend=clamp(dyeBlend*uDyeStrength,0.,1.);
  float rimTint=hintAmount>0.?uDyeStrength:min(amount,1.5);
  vec3 color=mix(bg*(film?1.:1.4),dye*.85,dyeBlend)*diff+spec*vec3(1.,.98,.95);
  color+=dye*rim*.22*rimTint;
  // Emission is independent of normal, light and dye release. Zero really is off.
  color+=cloud*uEmissivity*mix(dye,vec3(1.),.65);
  color+=.25*pulse*mix(dye,vec3(1.),.25);
  a*=film?mix(.48,.78,milk):mix(.96,1.,milk);
#ifdef GOO_CACHE
  // Every dye term above is the texture dye times a factor; this is that factor, out of the
  // control milk, which does not come from the texture.
  float dyeShare=hintAmount>0.?0.:(1.-milk*.8)*
    (.85*dyeBlend*diff+rim*.22*rimTint+cloud*uEmissivity*.35+.25*pulse*.75);
  vec4 cacheParams=vec4(clamp((refr-p)/32.+.5,0.,1.),
    clamp((1.-dyeBlend)*(film?1.:1.4)*diff/1.5,0.,1.),clamp(dyeShare/1.5,0.,1.));
#endif
#if defined(GOO_CACHE_BOTH)
  // GO26: one pass writes both caches (color and coverage; refraction and light).
  gl_FragColor=vec4(clamp(color,0.,1.),a);
  goo_params=cacheParams;
#elif defined(GOO_CACHE_PARAMS)
  gl_FragColor=cacheParams;
#elif defined(GOO_CACHE)
  // The background term is nonnegative, so clamping intrinsic light before compositing
  // gives the same final clamp.
  gl_FragColor=vec4(clamp(color,0.,1.),a);
#else
  gl_FragColor=vec4(clamp(color,0.,1.)*a,a);
#endif
}
)";
// The settled surface is independent of the scene beneath it. Cache its own
// color/coverage and the background's refraction/lighting coefficient; changing
// windows then need only this short composite rather than all SDF/depth work.
inline const std::string cached_composite_shader = R"(
precision highp float;
varying vec2 pos;
uniform sampler2D uIntrinsic,uRefraction,uBackground,uDyeTex;
uniform mat4 uBackgroundMap;
uniform vec2 uRes;
// GO24: the surface's color is its own light plus a share of the dye. The cache keeps
// the two apart (refraction alpha is the dye's share), and the dye is read here, so
// the dye can move without the surface being rendered again.
)" + dye_filter + R"(
void main(){
  vec2 uv=(uBackgroundMap*vec4(pos,0.,1.)).xy*.5+.5;
  vec4 intrinsic=texture2D(uIntrinsic,uv);
  if(intrinsic.a<=0.)discard;
  vec4 refr=texture2D(uRefraction,uv);
  vec2 shifted=pos+(refr.rg-.5)*32.;
  vec2 bgUV=(uBackgroundMap*vec4(shifted,0.,1.)).xy*.5+.5;
  vec3 bg=texture2D(uBackground,bgUV).rgb;
  vec3 color=clamp(intrinsic.rgb+refr.a*1.5*exp(-sampleDyeK(uDyeTex,pos/uRes))+refr.b*1.5*bg,0.,1.);
  gl_FragColor=vec4(color*intrinsic.a,intrinsic.a);
}
)";
// GO18: cross-fade two nearby breathing surfaces after compositing each over
// the current backdrop. Premultiplied results keep a shore from darkening as it
// appears in only one cached key.
inline const std::string cached_composite_mix_shader = R"(
precision highp float;
varying vec2 pos;
uniform sampler2D uIntrinsic,uRefraction,uIntrinsicB,uRefractionB,uBackground,uDyeTex;
uniform mat4 uBackgroundMap;
uniform vec2 uRes;
uniform float uMix;
)" + dye_filter + R"(
vec3 dye;
vec4 layer(vec4 intrinsic,vec4 refr){
  if(intrinsic.a<=0.)return vec4(0.);
  vec2 shifted=pos+(refr.rg-.5)*32.;
  vec2 bgUV=(uBackgroundMap*vec4(shifted,0.,1.)).xy*.5+.5;
  vec3 color=clamp(intrinsic.rgb+refr.a*1.5*dye+refr.b*1.5*texture2D(uBackground,bgUV).rgb,0.,1.);
  return vec4(color*intrinsic.a,intrinsic.a);
}
void main(){
  dye=exp(-sampleDyeK(uDyeTex,pos/uRes));
  vec2 uv=(uBackgroundMap*vec4(pos,0.,1.)).xy*.5+.5;
  vec4 a=texture2D(uIntrinsic,uv),b=texture2D(uIntrinsicB,uv);
  if(a.a<=0.&&b.a<=0.)discard;
  gl_FragColor=mix(layer(a,texture2D(uRefraction,uv)),layer(b,texture2D(uRefractionB,uv)),uMix);
}
)";
// Restores the cached backdrop where a breath is drawn without the scene beneath it
// having been repainted this frame.
inline const std::string backdrop_shader = R"(
precision highp float;
varying vec2 pos;
uniform sampler2D uBackground;
uniform mat4 uBackgroundMap;
void main(){gl_FragColor=texture2D(uBackground,(uBackgroundMap*vec4(pos,0.,1.)).xy*.5+.5);}
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
void main(){float h=decode(texture2D(uWave,uPoint/uRes)).x;gl_FragColor=vec4(exp(-sampleDyeK(uDyeTex,uPoint/uRes)),h/8.+128./255.);}
)";
inline const std::string copy_shader =
    "precision highp float; uniform sampler2D image; void main(){gl_FragColor=texture2D(image,vec2(.5));}";

// Program variants are chosen by #defines written into the sources above (GOO_FAST, GOO_CACHE,
// GOO_CACHE_PARAMS, GOO_CACHE_BOTH), never by searching and editing shader text: a change to a
// shader can then only fail to compile, which tests/goo-shader-variants-test.sh checks for
// every variant in both dialects, not throw at startup or silently stop matching.
struct program_variant
{
    const char *name;
    const std::string *fragment;
    const char *defines;
    bool derivatives = false;
    bool two_outputs = false;  // GLES 3 only: goo_color and goo_params
    bool es3_only = false;
    bool required = true;      // false: the renderer has a fallback when it does not link
};
inline const std::vector<program_variant> &program_variants()
{
    static const std::vector<program_variant> variants{
        {"field", &field_shader, ""}, {"mask", &mask_shader, ""}, {"wave", &wave_shader, ""},
        {"dye", &dye_shader, ""}, {"render", &render_shader, "", true},
        {"energy", &energy_shader, ""}, {"query", &query_shader, ""},
        {"field_fast", &field_shader, "#define GOO_FAST\n"}, {"mask_fast", &mask_shader, "#define GOO_FAST\n"},
        {"wave_fast", &wave_shader, "#define GOO_FAST\n"}, {"dye_fast", &dye_shader, "#define GOO_FAST\n"},
        {"render_fast", &render_shader, "#define GOO_FAST\n", true},
        {"intrinsic", &render_shader, "#define GOO_CACHE\n", true},
        {"refraction", &render_shader, "#define GOO_CACHE\n#define GOO_CACHE_PARAMS\n", true},
        {"cache_both", &render_shader, "#define GOO_CACHE\n#define GOO_CACHE_BOTH\n", true, true, true, false},
        {"composite", &cached_composite_shader, ""}, {"composite_mix", &cached_composite_mix_shader, ""},
        {"backdrop", &backdrop_shader, ""}, {"copy", &copy_shader, ""},
        {"under", &under_shader, ""}, {"change", &change_shader, ""},
    };
    return variants;
}
// GLES 2 sources are written in GLSL ES 1.00; GLES 3 gets the same text in 3.00 spelling.
inline std::string es3_spelling(std::string s, bool fragment)
{
    auto replace = [&s] (const std::string &from, const std::string &to)
    {
        for (size_t at = 0; (at = s.find(from, at)) != std::string::npos; at += to.size())
            s.replace(at, from.size(), to);
    };
    if (!fragment)
    {
        replace("attribute ", "in ");
        replace("varying ", "out ");
        return s;
    }
    replace("varying ", "in ");
    replace("texture2D(", "texture(");
    replace("gl_FragColor", "goo_color");
    replace("i<1024", "i<uCount");
    return s;
}
inline std::string vertex_source(bool es3)
{
    return es3 ? "#version 300 es\n" + es3_spelling(vertex, false) : "#version 100\n" + vertex;
}
inline std::string fragment_source(const program_variant &v, bool es3)
{
    if (es3)
        return std::string("#version 300 es\n") + v.defines + "precision highp float; " +
            (v.two_outputs ? "layout(location=0) out vec4 goo_color; layout(location=1) out vec4 goo_params;\n" :
                             "out vec4 goo_color;\n") + es3_spelling(*v.fragment, true);
    return std::string("#version 100\n") +
        (v.derivatives ? "#extension GL_OES_standard_derivatives : require\n" : "") + v.defines + *v.fragment;
}
} // namespace scottland::goo
