# UniLiDAR: Bridge the domain gap among different LiDARs for continual learning

# Abstract

LiDAR-based 3D perception algorithms have evolved rapidly alongside the emergence of large datasets. Nonetheless, considerable performance degradation often ensues when models trained on a specific dataset are applied to other datasets or real-world scenarios with different LiDAR.  
This paper aims to develop a unified model capable of handling different LiDARs, enabling continual learning across diverse LiDAR datasets and seamless deployment across heterogeneous platforms. We observe that the gaps among datasets primarily manifest in geometric disparities (such as variations in beams and point counts) and semantic inconsistencies (taxonomy conflicts). 
To this end, this paper proposes UniLiDAR, an occupancy prediction pipeline that leverages geometric realignment and semantic label mapping to facilitate multiple datasets training and mitigate performance degradation during deployment on heterogeneous platforms. Moreover, our method can be easily combined with existing 3D perception models. The efficacy of the proposed approach in bridging LiDAR domain gaps is verified by comprehensive experiments on two prominent datasets: OpenOccupancy-nuScenes and SemanticKITTI. UniLiDAR elevates the mIoU of occupancy prediction by $\textbf{15.7\%}$ and $\textbf{12.5\%}$, respectively, compared to the model trained on the directly merged dataset. Moreover, it outperforms several SOTA methods trained on individual datasets. We expect our research to facilitate further study of 3D generalization, the code will be available soon.

# Getting Started

- [Installation](docs/install.md) 

- [Prepare Dataset](docs/prepare_data.md)

- [Training, Evaluation, Visualization](docs/trainval.md)

# **overview  framework**

![framework](./framework.png)

# Acknowledgement

Many thanks to these excellent projects:
- [OpenOcuupancy](https://github.com/JeffWang987/OpenOccupancy)
- [SurroundOcc](https://github.com/weiyithu/SurroundOcc)
- [MonoScene](https://github.com/astra-vision/MonoScene)
- [Unidet](https://github.com/xingyizhou/UniDet)
