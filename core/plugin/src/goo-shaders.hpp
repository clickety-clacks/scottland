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
vec4 source(int i, float column) { return texture2D(uSources, vec2((column+.5)/6., (float(i)+.5)/float(max(uCount,1)))); }
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
float deposit(vec2 p,vec4 r,vec4 corners,vec4 dot) {
  float a=0.;
  for(int k=0;k<4;k++){
    vec2 c=r.xy+vec2(k==1||k==3?r.z:-r.z,k>=2?r.w:-r.w);
    float d=length(p-c)/30.; a+=corners[k]*.22*exp(-d*d);
  }
  float d=length(p-dot.xy)/12.; return a+dot.z*.45*exp(-d*d);
}
float gooField(vec2 p) {
  float F=0.;
  for(int i=0;i<1024;i++){
    if(i>=uCount)break;vec4 r=source(i,0.),g=source(i,1.);
    if(g.x<=0.)continue;
    float e=max(sdBox(p-r.xy,r.zw,g.y),0.),fe=fall(e);
    if(fe==0.)continue;
    float n=fbm(p*uNoiseScale+g.z*vec2(7.13,3.71)+vec2(uTime*uNoiseSpeed,-uTime*uNoiseSpeed*.73));
    float scale=clamp(abs(source(i,2.).w),0.,1.);
    float a=max(g.x*(1.+uNoise*scale*(n-.5)*2.),uT/max(fall(max(uThickness*.1*scale,source(i,5.).x)),.0001))
      +deposit(p,r,source(i,3.),source(i,4.));
    F+=max(a,0.)*fe;
  } return F;
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
float gooMask(vec2 uv){return smoothstep(uT*.97,uT*1.03,field(uv))*step(0.,unionSdf(uv*uRes));}
)";
inline const std::string field_shader = common + R"(
void main(){
  vec2 p=gl_FragCoord.xy*uRes/uSize;
  float d=unionSdf(p);
  // Deep inside a window the goo is hidden and never read: any value over the threshold will do.
  float f=d<-8.?uT*4.:gooField(p);
  // Log packing spends RGBA8 precision at the boundary, avoiding staircase edges.
  if(uPacked>.5)f=log(1.+f)/2.83321334;
  gl_FragColor=vec4(f,step(0.,d),0,1);
}
)";
// Mask at quarter-resolution texel centers for the two wave stencils. Dye
// retains its original SDF evaluations (including continuous advection positions).
inline const std::string mask_shader = common + mask + R"(
void main(){
  vec2 uv=gl_FragCoord.xy/uSize;
  float f=smoothstep(uT*.97,uT*1.03,field(uv));
  gl_FragColor=vec4(f>0.?step(0.,unionSdf(uv*uRes)):0.,0,0,1);
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
  h*=mix(1.,uDamp,uPacked);
  for(int k=0;k<8;k++){if(k>=uImpN)break;float dd=distance(p,uImp[k].xy);h+=uImp[k].z*exp(-dd*dd/(uImp[k].w*uImp[k].w))*m;}
  gl_FragColor=encode(clamp(vec2(h,v),-3.9,3.9));
}
)";
inline const std::string dye_shader = common + mask + R"(
uniform sampler2D uDyeTex;
uniform float uSpread,uSwirl,uRelease;
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
  float ksum=1e-4,maxK=0.;vec3 nearest=vec3(0);
  for(int i=0;i<1024;i++){
    if(i>=uCount)break;vec4 r=source(i,0.),g=source(i,1.);float e=max(sdBox(p-r.xy,r.zw,g.y),0.);
    float k=g.x*fall(e);maxK=max(maxK,k);ksum+=k;nearest+=k*source(i,2.).rgb;
  }
  for(int i=0;i<1024;i++){
    if(i>=uCount)break;vec4 r=source(i,0.),g=source(i,1.);float e=max(sdBox(p-r.xy,r.zw,g.y),0.);
    float k=g.x*fall(e); if(k<maxK-.00001)continue;
    float w=uRelease*g.w*exp(-e/(uReach*.6));
    vec3 tint=source(i,2.).rgb;
    // State marks are released dye, Gaussian deposits, never overlay geometry.
    float cloud=deposit(p,r,source(i,3.),vec4(source(i,4.).xy,0,0));
    float dd=length(p-source(i,4.).xy)/12.,dot=source(i,4.).z*exp(-dd*dd);
    vec3 mark=source(i,2.).w>0.?vec3(.08,.08,.1):vec3(1.);
    tint=mix(tint,mark,clamp(cloud*2.,0.,.8));
    tint=mix(tint,mark,clamp(dot,0.,1.));
    c=mix(c,tint,clamp(w,0.,1.));
  }
  c=mix(c,nearest/ksum,(1.-m)*.25);
  gl_FragColor=vec4(c,1);
}
)";
inline const std::string render_shader = common + mask + R"(
uniform sampler2D uWave,uDyeTex,uBackground;
uniform float uWaveAmp,uShine,uRelief,uAlpha,uHints;
uniform mat4 uBackgroundMap;
float height(vec2 uv){float F=field(uv)*(1.+uWaveAmp*decode(texture2D(uWave,uv)).x);return clamp(log(max(F,1e-4)/uT),0.,3.);}
void main(){
  vec2 p=pos,uv=p/uRes,px=1./uRes;
  float d=unionSdf(p); if(d<=0.)discard; // window interiors mask the goo: skip the field there
  float F=field(uv),h=decode(texture2D(uWave,uv)).x,Fe=F*(1.+uWaveAmp*h);
  float a=smoothstep(uT*.97,uT*1.03,Fe)*smoothstep(0.,1.,d)*uAlpha;
  if(a<=0.)discard;
  // WK14: window mode tints the goo with each hinted window's color at once, blended by
  // contribution so connected goo stays smooth; there is no separate rim.
  float hintAmount=0.;vec3 hintDye=vec3(0.);
  if(uHints>.5)for(int i=0;i<1024;i++){
    if(i>=uCount)break;if(source(i,5.).x<=0.)continue;
    vec4 r=source(i,0.),g=source(i,1.);float contribution=g.x*fall(max(sdBox(p-r.xy,r.zw,g.y),0.));
    hintDye+=source(i,2.).rgb*contribution;hintAmount+=contribution;
  }
  float ht=height(uv);
  vec3 n=normalize(vec3(-(height(uv+vec2(px.x,0))-ht)*uRelief,-(height(uv+vec2(0,px.y))-ht)*uRelief,1.));
  // Keep the shipped one-pixel exclusion around window content.
  vec2 refr=p+n.xy*26.; if(unionSdf(refr)<1.)refr=p;
  vec2 bgUV=(uBackgroundMap*vec4(refr,0,1)).xy*.5+.5;
  vec3 bg=texture2D(uBackground,bgUV).rgb,dye=texture2D(uDyeTex,uv).rgb;
  if(hintAmount>0.)dye=hintDye/hintAmount;
  vec3 L=normalize(vec3(-.45,-.55,.7));float diff=.6+.4*dot(n,L);
  float spec=pow(max(dot(reflect(-L,n),vec3(0,0,1)),0.),48.)*uShine;
  float rim=1.-smoothstep(0.,.5,ht);
  vec3 color=mix(bg*1.4,dye*.85,.55)*diff+spec*vec3(1.,.98,.95)+dye*rim*.22;
  gl_FragColor=vec4(clamp(color,0.,1.)*a*.96,a*.96);
}
)";
// Max-reduction of changes in dye and wave energy, read back as a single pixel every 30 steps.
inline const std::string energy_shader = common + R"(
uniform sampler2D uWave,uDyeTex,uPrevious,uReduce;
uniform int uFirst;
uniform vec2 uInputSize;
void main(){
  float e=0.;vec2 start=(gl_FragCoord.xy-.5)*2.;
  for(int y=0;y<2;y++)for(int x=0;x<2;x++){
    vec2 uv=(start+vec2(float(x),float(y))+.5)/uInputSize;
    float v;
    if(uFirst==1){vec2 hv=decode(texture2D(uWave,uv));vec3 dc=abs(texture2D(uDyeTex,uv).rgb-texture2D(uPrevious,uv).rgb);v=max(max(abs(hv.x),abs(hv.y)),max(max(dc.r,dc.g),dc.b)*16.);}
    else v=texture2D(uReduce,uv).r;
    e=max(e,v);
  }
  gl_FragColor=vec4(e,e,e,1);
}
)";
inline const std::string query_shader = common + R"(
uniform sampler2D uWave;
uniform vec2 uPoint;
uniform sampler2D uDyeTex;
void main(){float h=decode(texture2D(uWave,uPoint/uRes)).x;gl_FragColor=vec4(texture2D(uDyeTex,uPoint/uRes).rgb,h/8.+128./255.);}
)";
} // namespace scottland::goo
