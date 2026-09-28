from __future__ import annotations
from dataclasses import dataclass
import re
REFERENCE_JOINTS={"humanoid":["root","pelvis","spine","head","shoulder_l","upper_arm_l","forearm_l","hand_l","shoulder_r","upper_arm_r","forearm_r","hand_r","thigh_l","shin_l","foot_l","thigh_r","shin_r","foot_r"]}
def _key(x): return re.sub(r"[^a-z0-9]","",x.lower()).replace("left","l").replace("right","r")
@dataclass(frozen=True)
class MappingResult:
 mapping:dict[str,str]; unmapped:tuple[str,...]; ambiguous:dict[str,tuple[str,...]]
 @property
 def valid(self): return not self.unmapped and not self.ambiguous
def auto_map(source_joints,reference="humanoid"):
 required=REFERENCE_JOINTS[reference]; indexed={}
 for source in source_joints: indexed.setdefault(_key(source),[]).append(source)
 mapping={}; ambiguous={}; unmapped=[]
 for target in required:
  candidates=indexed.get(_key(target),[])
  if len(candidates)==1:mapping[target]=candidates[0]
  elif not candidates: unmapped.append(target)
  else: ambiguous[target]=tuple(candidates)
 return MappingResult(mapping,tuple(unmapped),ambiguous)


def _v(value, default):
    value=value if isinstance(value,(list,tuple)) and len(value)==3 else default
    return tuple(float(x) for x in value)

def _q(value):
    value=value if isinstance(value,(list,tuple)) and len(value)==4 else (0,0,0,1)
    return tuple(float(x) for x in value)

def _qmul(a,b):
    ax,ay,az,aw=a; bx,by,bz,bw=b
    return (aw*bx+ax*bw+ay*bz-az*by, aw*by-ax*bz+ay*bw+az*bx, aw*bz+ax*by-ay*bx+az*bw, aw*bw-ax*bx-ay*by-az*bz)

def _qinv(q):
    x,y,z,w=q; n=x*x+y*y+z*z+w*w or 1.0
    return (-x/n,-y/n,-z/n,w/n)

def _qnorm(q):
    n=sum(x*x for x in q)**0.5 or 1.0
    return tuple(x/n for x in q)

def retarget_pose(source_pose, mapping, source_rest=None, target_rest=None):
    """Retarget local joint transforms using rest-pose deltas.

    `mapping` maps target joint names to source names. Transforms are JSON-style
    dictionaries with optional translation, rotation quaternion, and scale.
    The source pose delta is applied to the target rest transform, with a bone
    length ratio for translations. This is deterministic authoring retargeting;
    it is not a Melee animation/runtime converter.
    """
    source_pose=source_pose or {}; source_rest=source_rest or {}; target_rest=target_rest or {}; output={}
    for target,source in mapping.items():
        if source not in source_pose and source not in source_rest: raise ValueError(f"source joint transform missing: {source}")
        sp=source_pose.get(source,source_rest.get(source,{})); sr=source_rest.get(source,{}) ; tr=target_rest.get(target,{})
        srest=_v(sr.get("translation"),(0,0,0)); spose=_v(sp.get("translation"),srest); trest=_v(tr.get("translation"),(0,0,0))
        source_len=sum(x*x for x in srest)**0.5; target_len=sum(x*x for x in trest)**0.5; ratio=target_len/source_len if source_len>1e-8 and target_len>1e-8 else 1.0
        delta=tuple((spose[i]-srest[i])*ratio for i in range(3)); rotation=_qnorm(_qmul(_q(tr.get("rotation")),_qmul(_q(sp.get("rotation")),_qinv(_q(sr.get("rotation"))))))
        ss=_v(sr.get("scale"),(1,1,1)); ps=_v(sp.get("scale"),ss); ts=_v(tr.get("scale"),(1,1,1)); scale=tuple(ts[i]*(ps[i]/ss[i] if abs(ss[i])>1e-8 else 1.0) for i in range(3))
        output[target]={"translation":[trest[i]+delta[i] for i in range(3)],"rotation":list(rotation),"scale":list(scale)}
    return output
