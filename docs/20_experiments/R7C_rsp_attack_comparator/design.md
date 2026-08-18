# R7C RSP Attack comparator：冻结设计

只运行AX01 raw `-RSP defense score`与唯一正式AX02 increasing isotonic `E[A4|-RSP]`。所有拟合严格使用R7A folds，最长13周purge；A4 primary使用4周moving-block，B4/W4只作否决。

本批不训练attack LightGBM、不增加Delta4、不生成状态或NAV。AX00仅登记为Round8的Y5-only控制，不在本批拟合。
