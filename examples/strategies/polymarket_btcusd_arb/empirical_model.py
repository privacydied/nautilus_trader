from dataclasses import dataclass
@dataclass(frozen=True)
class EmpiricalEstimate: probability_yes:float; source:str; bucket_n:int=0; reliable:bool=False
def clip_probability(v:float)->float: return max(0.0,min(1.0,float(v)))
class EmpiricalModel:
    def __init__(self,samples=(),reliable_bucket_n:int=50): self.samples=tuple(samples); self.reliable_bucket_n=reliable_bucket_n
    def estimate(self,fair_probability:float,**kwargs): return EmpiricalEstimate(clip_probability(fair_probability),'parametric_fallback',0,False)
